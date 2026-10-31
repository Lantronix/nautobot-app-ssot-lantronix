# nautobot-app-ssot-lantronix

A [Nautobot SSoT](https://github.com/nautobot/nautobot-app-ssot) integration that keeps Nautobot current with the Lantronix SLC 9000, SLC 8000 and EMG console servers managed by Percepxion. Nobody has to type console server inventory into Nautobot by hand.

The **Percepxion to Nautobot** job reads Percepxion and:

- creates each console server in Nautobot with the right device type, so its console server ports come from the device type
- labels each console server port with the name configured on the console server, and describes it with the hostname of the attached device
- records the port state and the attached device's type, model, OS version, serial, hostname and management IP in custom fields on the port, when the console server reports them
- adds an **Open console** link on each port that launches an SSH session through the console server (`ssh://<console server>:<base port + port number>`)

The job never writes to Percepxion or to a console server, and never deletes anything in Nautobot. Like every SSoT job, it can run as a dry run that shows the diff without writing.

Two optional modules build on the synced data:

- **ServiceNow console paths**: the stock nautobot-ssot ServiceNow job plus a "Connects to" relationship between each attached device and the console server port that reaches it.
- **Golden Config**: a Nornir dispatcher that backs up SLC configuration with every secret masked, for Nautobot Golden Config backup, intended and compliance jobs.

## Requirements

| | |
|---|---|
| Nautobot | 3.1 or later, below 4.0 |
| nautobot-ssot | 4.8 or later, below 5 |
| Python | 3.10 to 3.14 |
| Percepxion | Percepxion cloud or on-premises, and a Percepxion user with the **tenant_user** role in the organization you sync. No administrator role is needed. |
| Device types | Lantronix device types from the [NetBox devicetype-library](https://github.com/netbox-community/devicetype-library/tree/master/device-types/Lantronix) (the same YAML), imported before the first sync |

## Install

1. Install the package into the Nautobot environment:

   ```
   pip install nautobot-app-ssot-lantronix
   ```

   For the ServiceNow module, install `nautobot-app-ssot-lantronix[servicenow]`.

2. Enable and configure the app in `nautobot_config.py` (see [Configuration](#configuration)).

3. Run `nautobot-server post_upgrade` and restart Nautobot and its Celery worker.

4. Import the Lantronix device types for your hardware.

On the first run the app creates the manufacturer `Lantronix`, the platform `Lantronix ITM OS` (network driver `lantronix_itmgos`), the device role you configure, the custom fields it writes and the **Lantronix: Open console** custom link, and reuses any that already exist.

## Configuration

The Percepxion endpoint, organization and credentials come only from environment variables. They're never stored in the Nautobot database, job inputs or logs.

| Variable | |
|---|---|
| `PERCEPXION_URL` | API base, for example `https://api.percepxion.ai/api`. Must use `https://`. |
| `PERCEPXION_USERNAME`, `PERCEPXION_PASSWORD` | The Percepxion user the job signs in as |
| `PERCEPXION_ORG_ID` | The Percepxion organization to sync |
| `PERCEPXION_CA_BUNDLE` | Optional CA bundle for on-premises Percepxion |

```python
PLUGINS = ["nautobot_ssot", "nautobot_ssot_lantronix"]

PLUGINS_CONFIG = {
    "nautobot_ssot": {"hide_example_jobs": True},
    "nautobot_ssot_lantronix": {
        "default_location": "Main Site",   # location for new console servers (must exist)
        "default_role": "Console Server",
        "default_status": "Active",
        "ssh_base_port": 3000,             # change if you changed it on your console servers
        "model_rules": {},                 # see Device models
    },
}
```

A missing setting stops the job with an error that names the setting, never its value.

### Device models

Percepxion reports an SLC 9000's model as its total number of serial ports and doesn't report the Ethernet switch module or the power supply type, so several SLC 9000 SKUs look the same in Percepxion. The job picks the device type from `show sysconfig` output stored in Percepxion (if anyone has run `show sysconfig` on the unit through Percepxion, in the console editor or a CLI job), then from `model_rules`, then from a single device type that matches the port counts. A rule maps `MODEL|RJ45|USB` to a device type, for fleets that buy one variant:

```python
"model_rules": {"SLC9016|16|0": "lantronix-slc9016-16e"}
```

A device the job can't resolve isn't created; the job log names it and the candidate types. EMG units resolve from Percepxion data alone.

### What the job changes in Nautobot

| Object | Job writes | Job never changes |
|---|---|---|
| Console server (device) | created with name, type, location, role, platform, serial and the Percepxion device ID; management IP in a custom field, never in IPAM | location, role or type of an existing device |
| Console server port | label (the port's name on the console server), description (attached device hostname), port state and attached-device custom fields | name, cables, anything else |

The console server is the source of truth for port labels and descriptions. A label edited by hand in Nautobot is overwritten on the next sync; rename the port on the console server instead. An existing Nautobot device is adopted only when it's a Lantronix device on the Lantronix platform (or none) whose serial matches, or that has no serial and the same name; anything else with the same name is reported and left alone.

## Open console

The link builds `ssh://<address>:<SSH base port + port number>` from the device's primary IP, or the management address Percepxion reports. Your browser opens it with your SSH client and you sign in to the console server as yourself.

On the console server, direct SSH access is off by default on every port. Turn it on for the ports you want to reach, for example `set deviceport port 1-48 sshin enable` (answer `yes` to the confirmation).

## ServiceNow console paths (optional)

With nautobot-ssot's ServiceNow integration enabled (`"enable_servicenow": True` under `nautobot_ssot`, with its instance and credential settings), the app registers **Nautobot ⟹ ServiceNow (with console paths)**. It runs the stock device, interface, model and location sync, then adds a `cmdb_rel_ci` "Connects to::Connected by" relationship from each attached device to its console server, with the port number. Both CIs must already be in ServiceNow. The job never deletes records; links in ServiceNow with no Nautobot cable behind them are logged.

Set `"servicenow_console_link_parent": "console_server"` under `nautobot_ssot_lantronix` to make the console server the parent of each link instead.

## Golden Config (optional)

With Nautobot Golden Config installed, map the platform's network driver to the app's dispatcher:

```python
PLUGINS_CONFIG["nautobot_plugin_nornir"] = {
    "dispatcher_mapping": {
        "lantronix_itmgos": "nautobot_ssot_lantronix.golden_config.dispatcher.NetmikoLantronixItmgos",
    },
}
```

Backups use the console server's own configuration export and need a Netmiko release that includes the `lantronix_itmgos` driver. Every password, community and key is replaced with `<secret hidden>`, and a backup that can't be proven fully masked is refused rather than saved.

## License

Apache-2.0
