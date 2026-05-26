# PATLITE LR6-USB Spot Extension

Boston Dynamics Spot extension for controlling a PATLITE LR6-USB signal tower from mission actions.

The extension runs a Spot `RemoteMissionService` inside a Docker container. Mission nodes can turn individual LR6-USB light segments on or off, set flash patterns, control all tower segments together, and run buzzer patterns.

## Repository Contents

| Path | Description |
| --- | --- |
| `Extension/` | Spot extension package contents |
| `Extension/patlite_lr6.spx` | Prebuilt Spot extension archive |
| `Extension/patlite_lr6.tgz` | Prebuilt Docker image archive referenced by the extension manifest |
| `Extension/manifest.json` | Spot extension metadata |
| `Extension/docker-compose.yml` | Container runtime configuration |
| `Extension/99-patlite-usb.rules` | Linux udev rule for the PATLITE USB HID device |
| `Extension/src/main.py` | Direct PATLITE LR6-USB Python controller and CLI |
| `Extension/src/robot_command_mission_service.py` | Spot RemoteMissionService implementation |
| `LR-USB USB*.pdf` | PATLITE LR6-USB protocol/reference documents |

## Hardware

- PATLITE LR6-USB signal tower
- Linux host or Spot CORE/extension runtime with USB access to the tower
- Boston Dynamics Spot robot when using the RemoteMissionService extension

The controller targets the PATLITE USB device with:

- Vendor ID: `0x191A`
- Product ID: `0x8003`

## Extension Behavior

The service registers with Spot Directory Registration as:

- Directory name: `patlite-remote-service`
- Authority: `remote-mission`
- Service type: `bosdyn.api.mission.RemoteMissionService`

Mission custom parameters expose these commands:

| Command | Purpose |
| --- | --- |
| `noop` | Keep the mission action running without changing the tower |
| `off` | Turn off the lights and stop the buzzer |
| `single` | Set one color segment |
| `tower` | Set red, yellow, and green segments together |
| `buzzer` | Run a buzzer pattern |
| `buzzer_ex` | Run a buzzer pattern with custom pitches |

Supported light states:

- `off`
- `on`
- `flash1`
- `flash2`
- `flash3`
- `flash4`
- `keep`

Supported colors:

- `red`
- `yellow`
- `green`

Supported buzzer patterns:

- `off`
- `on`
- `pattern1`
- `pattern2`
- `pattern3`
- `pattern4`

The buzzer limit accepts values from `0` to `15`. `0` is continuous; `1` through `15` follow the PATLITE protocol repeat behavior.

## Install the Spot Extension

1. Connect the PATLITE LR6-USB tower to the extension host USB port.
2. Upload `Extension/patlite_lr6.spx` through the Spot web admin extension page.
3. Start the extension from the Spot web admin interface.
4. Confirm the service appears in Spot directory registration as `patlite-remote-service`.
5. Add a Remote Mission Service node in a mission and select the PATLITE command parameters.

## Configuration

The container expects Spot credentials and host configuration. The included `Extension/docker-compose.yml` contains example values:

```yaml
environment:
  SPOT_HOSTNAME: "192.168.50.3"
  SPOT_USERNAME: "user"
  SPOT_PASSWORD: "change-me"

command:
  - "python3"
  - "/app/robot_command_mission_service.py"
  - "--host-ip"
  - "192.168.50.5"
  - "--port"
  - "21222"
  - "192.168.50.3"
```

Before deploying, update:

- `SPOT_HOSTNAME` or the final hostname argument to match the robot address.
- `SPOT_USERNAME` and `SPOT_PASSWORD` for the Spot account used by the service.
- `--host-ip` to the IP address Spot can use to reach the extension service.
- `--port` if `21222` is already in use.

Do not commit real robot credentials to a public repository.

## USB Permissions

The extension includes a udev rule:

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="191a", ATTR{idProduct}=="8003", MODE="0666", GROUP="plugdev"
```

On a Linux development machine, install it with:

```bash
sudo cp Extension/99-patlite-usb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and reconnect the LR6-USB tower.

## Run Locally for Development

Install dependencies:

```bash
python3 -m pip install pyusb bosdyn-api bosdyn-core bosdyn-client deprecated requests pyjwt pynacl protobuf
```

Run direct PATLITE commands without Spot:

```bash
python3 Extension/src/main.py single red on
python3 Extension/src/main.py tower --red on --yellow off --green flash1
python3 Extension/src/main.py buzzer pattern1 3
python3 Extension/src/main.py off
```

Run the RemoteMissionService locally:

```bash
export SPOT_USERNAME="user"
export SPOT_PASSWORD="password"

python3 Extension/src/robot_command_mission_service.py \
  --host-ip 192.168.50.5 \
  --port 21222 \
  192.168.50.3
```

## Build the Docker Image

From the extension directory:

```bash
cd Extension
docker build -f Dockerfile.l4t -t patlite_lr6:1.0 .
docker save patlite_lr6:1.0 | gzip > patlite_lr6.tgz
```

The generated `patlite_lr6.tgz` is referenced by `manifest.json`.

## License

See [LICENSE.txt](LICENSE.txt) for the PATLITE software licensing terms.

## References

- [PATLITE LR6-USB product page](https://www.patlite.com/product/detail0000000689.html)
- [PATLITE contact page](https://www.patlite.com/contact/english/input.html)
