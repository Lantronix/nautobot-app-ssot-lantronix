"""Nornir dispatcher for Golden Config backups of Lantronix ITMG OS devices.

Point Golden Config at it with
PLUGINS_CONFIG["nautobot_golden_config"]["custom_dispatcher"] = {
    "lantronix_itmgos": "nautobot_ssot_lantronix.golden_config.dispatcher.NetmikoLantronixItmgos"}.
Read only: it runs the configuration export and answers its confirmation prompt, nothing else.
"""

from __future__ import annotations

import re

from nornir.core.task import Result, Task
from nornir_nautobot.exceptions import NornirNautobotException
from nornir_nautobot.plugins.tasks.dispatcher.default import NetmikoDefault

from .export import CONFIRM_PROMPT, EXPORT_COMMAND, UnmaskedSecretError, clean_export

READ_TIMEOUT = 180  # a 48-port unit with many settings takes a while to generate the file


class NetmikoLantronixItmgos(NetmikoDefault):
    config_command = EXPORT_COMMAND

    @classmethod
    def get_config(  # noqa: PLR0913 - signature fixed by nornir-nautobot
        cls,
        task: Task,
        logger,
        obj,
        backup_file: str,
        remove_lines: list,
        substitute_lines: list,
        command_file_path: str = None,
    ) -> Result:
        connection = task.host.get_connection("netmiko", task.nornir.config)
        prompt = re.escape(connection.find_prompt())
        # Raw channel I/O: Netmiko's send_command returns the device to its prompt before the
        # confirmation is answered, so "yes" would arrive as a new command (lab, 2026-10-30).
        connection.write_channel(EXPORT_COMMAND + connection.RETURN)
        first = connection.read_until_pattern(pattern=CONFIRM_PROMPT, read_timeout=30)
        connection.write_channel("yes" + connection.RETURN)
        rest = connection.read_until_pattern(pattern=prompt, read_timeout=READ_TIMEOUT)
        try:
            # clean_export strips ANSI, folds raw CR/LF mixes and refuses unmasked secrets.
            config = clean_export(first + "\n" + rest)
        except UnmaskedSecretError as exc:
            msg = f"{obj.name}: backup not saved, secret masking check failed ({exc})"
            logger.error(msg, extra={"object": obj})
            raise NornirNautobotException(msg) from None
        if not config.strip() or "set " not in config:
            msg = f"{obj.name}: the configuration export returned no configuration"
            logger.error(msg, extra={"object": obj})
            raise NornirNautobotException(msg)
        processed = cls._process_config(logger, config, remove_lines, substitute_lines, backup_file)
        return Result(host=task.host, result={"config": processed})
