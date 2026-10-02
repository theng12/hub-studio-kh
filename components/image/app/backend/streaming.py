"""Run a model's transformer blocks one at a time, so only one block's weights are ever resident.

Why.  On an 8 GB Mac the Image worker's problem is not the size of any one file, it is the *hot working
set*: the weights a denoise step touches every step.  Z-Image-Turbo 4-bit's transformer is 3.46 GB
(34 blocks of 102 MB) and its activations are another 1.7 GB, so the loop's working set is ~6 GB on a Mac
that has ~4 GB to give; the kernel then evicts the weights it needs next (the loop visits them in the
same order every step, which is exactly the access pattern LRU evicts worst), writing each one to swap
and reading it back: measured 9.6-33 GB of swap-outs, and 349 s an image against 166-236 s on a big Mac.

A weight that is only *read* is free to drop: if each block's weights are loaded from the model's file when
the block runs and released when it is done, nothing is ever dirty, nothing is ever written to swap, and the
price is reading the file again each step (the SSD, not the swap file; the page cache serves it when there is
RAM to spare, and an internal SSD serves the rest at 2-3 GB/s).  A pass that visits every weight exactly once
(the text encoder, once per image) costs nothing at all: each block is evaluated, then released.

How.  mflux loads weights lazily (``mx.load`` reads a file when an array is first evaluated), so a block's
weights can be put back as *unevaluated* arrays and read again on demand.  :func:`apply` replaces each block
in the named lists with a :class:`_Streamed` proxy that:

1. gives the block its weights (a ``multi_pass`` component re-opens its weight files for this: a fresh,
   unevaluated tree, a few milliseconds),
2. runs the block and evaluates its output (so MLX does not build one graph over every block and load them
   all at once),
3. replaces the block's weights with one-element placeholders, which frees them.

Same weights, same operations, same order: the output is the model's, not an approximation.  What does change
is that the loop must not be wrapped in ``mx.compile`` (evaluation inside a compiled function is an error), so
:mod:`staging` builds the denoise function with mflux's non-compiled variant when a model is streamed - which
is the variant mflux itself uses on M1/M2 Macs, the 8 GB tier.
"""
from __future__ import annotations

import sys


def _mx():
    return sys.modules['mlx.core']


def _tree_map(function, tree):
    from mlx.utils import tree_map
    return tree_map(function, tree)


class _Source:
    """The lazy weight tree of one component, re-opened for each pass over its blocks."""

    def __init__(self, loader) -> None:
        self.loader = loader
        self.tree = None

    def take(self, group: str, index: int):
        """This block's unevaluated weights, opening the files again when this pass has used them."""
        entry = self.tree[group][index] if self.tree is not None else None
        if entry is None:
            self.tree = self.loader()
            entry = self.tree[group][index]
        self.tree[group][index] = None
        return entry

    def close(self) -> None:
        self.tree = None


class _Streamed:
    """Stands in for one block in its parent's list; not a module, never traversed by one."""

    def __init__(self, block, group: str, index: int, source: _Source | None) -> None:
        self.block, self.group, self.index, self.source = block, group, index, source
        mx = _mx()
        self.empty = _tree_map(lambda array: mx.zeros((1,), array.dtype), block.parameters())

    def __call__(self, *args, **kwargs):
        mx = _mx()
        if self.source is not None:
            self.block.update(self.source.take(self.group, self.index))
        out = self.block(*args, **kwargs)
        mx.eval(out)
        self.block.update(self.empty)
        return out


def supports(component, groups) -> bool:
    return component is not None and all(isinstance(getattr(component, group, None), list)
                                         and getattr(component, group) for group in groups)


def apply(component, groups, *, loader=None) -> _Source | None:
    """Stream the blocks of ``component``'s ``groups`` lists; returns the weight source to close afterwards.

    ``loader`` (``() -> lazy weight tree``) makes the component multi-pass: its blocks get their weights back
    for every pass.  Without one, a block's weights are used once and released - right for a component that is
    called once per image.
    """
    source = _Source(loader) if loader is not None else None
    for group in groups:
        blocks = getattr(component, group)
        for index, block in enumerate(blocks):
            blocks[index] = _Streamed(block, group, index, source)
    return source


def component_loader(definition_path: str, component_name: str, model_dir):
    """``() -> the component's lazy weight tree``, through mflux's own loader and mapping.

    ``definition_path`` is ``'package.module:Class'`` of the model's weight definition, resolved here and not
    imported at module load (mflux is only importable inside the pinned image runtime).
    """
    import importlib
    from pathlib import Path

    module_name, _, class_name = definition_path.partition(':')
    definition = getattr(importlib.import_module(module_name), class_name)
    component = next(item for item in definition.get_components() if item.name == component_name)
    from mflux.models.common.weights.loading.weight_loader import WeightLoader
    root = Path(model_dir)

    def load():
        return WeightLoader.load_single_local(component=component, root_path=root).components[component_name]

    return load
