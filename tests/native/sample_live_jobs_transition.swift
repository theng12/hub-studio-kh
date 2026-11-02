// Usage: swift tests/native/sample_live_jobs_transition.swift <output.json>
// Records geometry only; it never reads job text, tokens or model libraries.
import AppKit
import ApplicationServices
precondition(CommandLine.arguments.count == 2, "Pass the output JSON path")
let app = NSWorkspace.shared.runningApplications.first { $0.localizedName == "StudioHub" || $0.localizedName == "Studio Hub" }!
let root = AXUIElementCreateApplication(app.processIdentifier)
func attr(_ node: AXUIElement, _ key: CFString) -> CFTypeRef? { var v: CFTypeRef?; AXUIElementCopyAttributeValue(node,key,&v); return v }
func label(_ node: AXUIElement) -> String { attr(node,kAXTitleAttribute as CFString) as? String ?? attr(node,kAXDescriptionAttribute as CFString) as? String ?? attr(node,kAXValueAttribute as CFString) as? String ?? "" }
func nodes(_ node: AXUIElement, depth: Int = 0) -> [AXUIElement] {
 guard depth < 12 else { return [] }
 return [node] + (attr(node,kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []).flatMap { nodes($0,depth:depth+1) }
}
func frame(_ node: AXUIElement) -> [Double] {
 var p=CGPoint.zero; var s=CGSize.zero
 if let v=attr(node,kAXPositionAttribute as CFString), CFGetTypeID(v)==AXValueGetTypeID() { AXValueGetValue(v as! AXValue,.cgPoint,&p) }
 if let v=attr(node,kAXSizeAttribute as CFString), CFGetTypeID(v)==AXValueGetTypeID() { AXValueGetValue(v as! AXValue,.cgSize,&s) }
 return [p.x,p.y,s.width,s.height]
}
let all=nodes(root)
guard let group=all.first(where: { (attr($0,kAXRoleAttribute as CFString) as? String)=="AXRadioGroup" && label($0).contains("History") }),
      let window=(attr(root,kAXWindowsAttribute as CFString) as? [AXUIElement])?.first else { fatalError("Open Jobs first") }
let tabs=attr(group,kAXChildrenAttribute as CFString) as! [AXUIElement]
let names=tabs.map(label)
let original=tabs.firstIndex { (attr($0,kAXValueAttribute as CFString) as? NSNumber)?.intValue == 1 }
let title=all.first { (attr($0,kAXRoleAttribute as CFString) as? String)=="AXStaticText" && label($0)=="Jobs" && frame($0)[0]>230 }
var samples=[[String:Any]](); var events=[[String:Any]]()
app.activate()
RunLoop.current.run(until: Date().addingTimeInterval(0.25))
precondition(NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
             "Hub must be frontmost before recording mouse clicks")
let start=ProcessInfo.processInfo.systemUptime
let sequence:[(Double,String)]=[(0.25,"Video"),(0.85,"Audio"),(1.45,"Video")]
var next=0
for tick in 0..<126 {
 let deadline=start+Double(tick)/60
 RunLoop.current.run(until:Date().addingTimeInterval(max(0,deadline-ProcessInfo.processInfo.systemUptime)))
 let t=ProcessInfo.processInfo.systemUptime-start
 if next<sequence.count && t>=sequence[next].0 {
  let name=sequence[next].1; let index=names.firstIndex(of:name)!
  precondition(NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
               "Hub lost focus; refusing a mouse click into another app")
  let rect=frame(tabs[index])
  let point=CGPoint(x:rect[0]+rect[2]/2,y:rect[1]+rect[3]/2)
  CGEvent(mouseEventSource:nil,mouseType:.leftMouseDown,mouseCursorPosition:point,mouseButton:.left)!.post(tap:.cghidEventTap)
  CGEvent(mouseEventSource:nil,mouseType:.leftMouseUp,mouseCursorPosition:point,mouseButton:.left)!.post(tap:.cghidEventTap)
  events.append(["t":t,"select":name,"method":"mouse"]); next += 1
 }
 samples.append(["t":ProcessInfo.processInfo.systemUptime-start,"window":frame(window),"bar":frame(group),"title":title.map(frame) ?? [],"segments":tabs.map(frame),"selected":tabs.map { (attr($0,kAXValueAttribute as CFString) as? NSNumber)?.intValue ?? -1 }])
}
if let original { AXUIElementPerformAction(tabs[original],kAXPressAction as CFString) }
let output:[String:Any]=["pid":app.processIdentifier,"screenCaptureAccess":CGPreflightScreenCaptureAccess(),"tabNames":names,"events":events,"samples":samples]
let data=try JSONSerialization.data(withJSONObject:output,options:[.prettyPrinted,.sortedKeys])
let path=CommandLine.arguments[1]
try data.write(to:URL(fileURLWithPath:path))
print("Saved",samples.count,"samples to",path)
print("Events",events)
for key in ["window","bar","title"] { print(key,"unique frames:",Set(samples.map { String(describing:$0[key]!) })) }
print("Segment geometry variants:",Set(samples.map { String(describing:$0["segments"]!) }).count)
print("Observed selections:",Set(samples.map { String(describing:$0["selected"]!) }))
