"""Log the tuya module in to the Smart Life cloud, and list the account's devices.

    docker compose run --rm tuya python tuya_login.py          # log in, then list
    docker compose run --rm tuya python tuya_login.py --list   # list only

The login is the one of the Home Assistant integration: the user code from the
Smart Life (or Tuya Smart) app, then a QR code scanned with that app. The
result is stored in TUYA_TOKEN_FILE, where the running module picks it up.

The list shows every device of the account with its device_key, what the
module would publish for its current status, and the rows for the devices
table (the name from the app, to be changed at will).
"""

from __future__ import annotations

import sys

import segno
from tuya_sharing import LoginControl

from tuya_publisher import Login, Settings, format_value, make_manager, selected

# How often to ask the cloud whether the code was scanned before giving up.
# The code expires within minutes.
ATTEMPTS = 5


def log_in(settings: Settings) -> Login | None:
    print("User code: in the Smart Life app, Me > gear icon (top right) > Account and Security > User Code")
    user_code = input("User code: ").strip()
    if not user_code:
        return None

    control = LoginControl()
    response = control.qr_code(settings.client_id, settings.app_schema, user_code)
    if not response.get("success"):
        print(f"No QR code: {response.get('msg', response)} (code {response.get('code')})", file=sys.stderr)
        return None
    token = response["result"]["qrcode"]

    print("\nScan this with the Smart Life app (scan icon on the home tab) and confirm the login there:\n")
    segno.make(f"tuyaSmart--qrLogin?token={token}", error="q").terminal(compact=True)

    for _ in range(ATTEMPTS):
        input("\nPress Enter once the login is confirmed in the app ")
        success, info = control.login_result(token, settings.client_id, user_code)
        if success:
            print(f"Logged in as {info.get('username', '?')}")
            return Login(
                user_code=user_code,
                terminal_id=info["terminal_id"],
                endpoint=info["endpoint"],
                token_info={
                    key: info[key] for key in ("t", "uid", "expire_time", "access_token", "refresh_token")
                },
            )
        print(f"Not logged in yet: {info.get('msg', info)}")
    return None


def list_devices(settings: Settings, login: Login) -> None:
    manager = make_manager(settings, login)
    manager.update_device_cache()
    if not manager.device_map:
        print("The account has no devices.")
        return

    module = settings.mqtt_topic_prefix.rsplit("/", 1)[-1]
    devices = set(settings.devices)
    published = [device for device in manager.device_map.values() if selected(device.id, devices)]
    for device in manager.device_map.values():
        state = "published" if device in published else "not in TUYA_DEVICES"
        print(f"\n{module}/{device.id}  {device.name}")
        print(f"  {device.category}, {device.product_name}, online {device.online}, {state}")
        for code, value in device.status.items():
            print(f"  {code} = {format_value(device, code, value)}")

    if published:
        print("\nRows for the devices table (then: docker compose restart ingest):")
        print("INSERT INTO devices (type, device_key, name) VALUES")
        print(",\n".join(
            "  ('tuya', '{}/{}', '{}')".format(module, device.id, device.name.replace("'", "''"))
            for device in published
        ) + "\nON CONFLICT (device_key) DO NOTHING;")


def main() -> int:
    settings = Settings()

    if "--list" in sys.argv[1:]:
        login = Login.load(settings.token_file)
        if login is None:
            print(f"No login in {settings.token_file}; run without --list first.", file=sys.stderr)
            return 1
    else:
        login = log_in(settings)
        if login is None:
            return 1
        login.save(settings.token_file)
        print(f"Stored in {settings.token_file}. A running tuya service picks it up within 30 s;")
        print("if it was already logged in: docker compose restart tuya")

    list_devices(settings, login)
    return 0


if __name__ == "__main__":
    sys.exit(main())
