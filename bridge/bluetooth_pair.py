"""Run the system Bluetooth pairing panel inside a separate app process."""
import json
import sys


def run_pairing_panel(action="pair"):
    import objc
    from AppKit import NSApplication, NSApplicationActivationPolicyRegular

    objc.loadBundle("IOBluetooth", globals(),
                    bundle_path="/System/Library/Frameworks/IOBluetooth.framework")
    objc.loadBundle("IOBluetoothUI", globals(),
                    bundle_path="/System/Library/Frameworks/IOBluetoothUI.framework")
    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    if action == "pair":
        controller = objc.lookUpClass("IOBluetoothPairingController").pairingController()
        controller.setTitle_("配对蓝牙串口模块")
        controller.setDescriptionText_("选择尚未配对的蓝牙串口模块")
    else:
        controller = objc.lookUpClass("IOBluetoothDeviceSelectorController").deviceSelector()
        controller.setTitle_("选择附近的蓝牙串口模块")
        controller.setDescriptionText_("选择要连接到 K210 的蓝牙串口模块")
    controller.addAllowedUUID_(objc.lookUpClass("IOBluetoothSDPUUID").uuid16_(0x1101))
    application.activateIgnoringOtherApps_(True)
    controller.runModal()
    results = controller.getResults() or []
    if not results:
        return {"ok": False, "cancelled": True}
    device = results[0]
    address = str(device.addressString()).replace("-", ":").lower()
    return {"ok": True, "device": {"address": address,
                                     "name": str(device.nameOrAddress())}}


def main(action="pair"):
    try:
        result = run_pairing_panel(action)
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") or result.get("cancelled") else 1


if __name__ == "__main__":
    sys.exit(main())
