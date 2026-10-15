@main
struct CatalogArrayProof {
    static let minimalVisionCatalog = Data("""
    {
      "models": [
        {
          "repo": "fixture/vision-array",
          "label": "Fixture Vision Array",
          "lane_contract": {
            "schema": "studiohub.lane-contract.v1",
            "modality": "vision",
            "operation": "image.analysis",
            "parameters": [
              {
                "name": "regions",
                "type": "array",
                "required": false,
                "max_items": 8,
                "default": ["sky", "water", "clouds", "fire"]
              }
            ]
          }
        }
      ]
    }
    """.utf8)

    static func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        guard condition() else {
            FileHandle.standardError.write(Data(("FAIL: " + message + "\n").utf8))
            exit(1)
        }
    }

    static func path(_ context: DecodingError.Context) -> String {
        context.codingPath.map(\.stringValue).joined(separator: ".")
    }

    fileprivate static func decodeCatalog(_ data: Data, with decoder: JSONDecoder) -> CatalogResponse {
        do {
            return try decoder.decode(CatalogResponse.self, from: data)
        } catch let DecodingError.dataCorrupted(context) {
            FileHandle.standardError.write(Data(("catalog_decode=dataCorrupted path=" + path(context) + "\n").utf8))
            exit(1)
        } catch let DecodingError.typeMismatch(type, context) {
            FileHandle.standardError.write(Data(("catalog_decode=typeMismatch type=\(type) path=" + path(context) + "\n").utf8))
            exit(1)
        } catch let DecodingError.keyNotFound(key, context) {
            FileHandle.standardError.write(Data(("catalog_decode=keyNotFound key=\(key.stringValue) path=" + path(context) + "\n").utf8))
            exit(1)
        } catch let DecodingError.valueNotFound(type, context) {
            FileHandle.standardError.write(Data(("catalog_decode=valueNotFound type=\(type) path=" + path(context) + "\n").utf8))
            exit(1)
        } catch {
            FileHandle.standardError.write(Data("catalog_decode=unknown\n".utf8))
            exit(1)
        }
    }

    static func main() throws {
        require(CommandLine.arguments.count == 1 || CommandLine.arguments.count == 2,
                "optionally pass the retained catalog-shape JSON path")
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        var retainedCount = 0
        if CommandLine.arguments.count == 2 {
            let retainedData = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
            let retained = decodeCatalog(retainedData, with: decoder)
            retainedCount = retained.models.count
            require(retained.models.count == 43, "expected the retained 43-model shape")
            require(retained.models.indices.contains(25)
                    && retained.models[25].laneContract?.parameters.indices.contains(4) == true
                    && retained.models[25].laneContract?.parameters[4].defaultValue != nil,
                    "the retained failing path is absent")
            let captured = retained.models[25].laneContract!.parameters[4].defaultValue!
            let capturedEncoded = try JSONEncoder().encode(captured)
            let capturedJSON = try JSONSerialization.jsonObject(with: capturedEncoded)
            let capturedArray = capturedJSON as? [Any]
            require(capturedArray?.count == 4
                    && capturedArray?.allSatisfy { $0 is String } == true,
                    "the retained four-string regions default was not preserved as an array")
            let capturedRoundTrip = try decoder.decode(NativeJSONValue.self, from: capturedEncoded)
            require(capturedRoundTrip == captured, "the retained array default did not round trip")
        }

        let catalog = decodeCatalog(minimalVisionCatalog, with: decoder)
        require(catalog.models.count == 1, "expected the literal Vision catalog row")
        require(catalog.models[0].laneContract?.parameters.count == 1
                && catalog.models[0].laneContract?.parameters[0].defaultValue != nil,
                "the literal Vision regions default is absent")
        let arrayValue = catalog.models[0].laneContract!.parameters[0].defaultValue!
        let parameter = catalog.models[0].laneContract!.parameters[0]

        let scalars: [(String, NativeJSONValue)] = [
            ("true", .bool(true)), ("1", .integer(1)), ("1.5", .number(1.5)),
            ("\"text\"", .string("text")), ("null", .null),
        ]
        for (json, expected) in scalars {
            let value = try decoder.decode(NativeJSONValue.self, from: Data(json.utf8))
            require(value == expected, "scalar type changed for \(json)")
            let encoded = try JSONEncoder().encode(value)
            let roundTrip = try decoder.decode(NativeJSONValue.self, from: encoded)
            require(roundTrip == expected, "scalar round trip changed for \(json)")
        }

        let arrayEncoded = try JSONEncoder().encode(arrayValue)
        let arrayJSON = try JSONSerialization.jsonObject(with: arrayEncoded)
        require((arrayJSON as? [String]) == ["sky", "water", "clouds", "fire"],
                "array encoding coerced its value")
        let arrayRoundTrip = try decoder.decode(NativeJSONValue.self, from: arrayEncoded)
        require(arrayRoundTrip == arrayValue, "array did not round trip")
        require(arrayValue.fieldText == "[sky, water, clouds, fire]", "array display is not stable")

        do {
            _ = try decoder.decode(NativeJSONValue.self, from: Data("{\"region\":\"sky\"}".utf8))
            require(false, "object support was added without an established need")
        } catch DecodingError.dataCorrupted(_) {
            // The intended strict boundary.
        }

        let optionParameterData = Data("""
        {"name":"regions","type":"array","required":false,
         "enum":[["sky","water"]],"default":["sky","water","clouds","fire"]}
        """.utf8)
        let optionParameter = try decoder.decode(NativeLaneParameter.self, from: optionParameterData)
        require(optionParameter.optionValues?.count == 1 && optionParameter.options == [],
                "a composite enum entry became a picker string")
        let row = NativeModelDefaults(
            repo: "fixture/vision", defaults: ["regions": arrayValue],
            stated: ["regions": arrayValue], chosen: [:], notUsed: [:],
            parameters: [parameter])
        let control = NativeWorkspaceStore.modelDefaultControls(row).first
        require(control?.kind == .unrenderable && control?.sendsValue == false,
                "an unsupported array editor became writable")
        require(control?.boundText == "published as array · default [sky, water, clouds, fire]",
                "the array default is not truthfully displayed")
        require(NativeModel.parameterSummary(parameter)
                == "published as array · default [sky, water, clouds, fire]",
                "the parameter fact lost its array default")
        let facts = NativeModel.limitFacts([
            "regions": NativeModelLimit(value: arrayValue, source: "lane_contract.model_limits")
        ])
        require(facts.first?.value == "[sky, water, clouds, fire]",
                "array fact display is incomplete")

        print("Native catalog array proof passed: fixture=1 retained=\(retainedCount) array=4 object=rejected editor=unrenderable")
    }
}
