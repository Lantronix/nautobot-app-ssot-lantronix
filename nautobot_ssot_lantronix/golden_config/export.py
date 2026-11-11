"""Clean and mask the ITMG OS configuration export. No Nautobot or Nornir imports.

The backup is the device's own `admin config commands copypaste` export: the CLI commands
that `admin config batch` restores. The export prints secrets in cleartext, so every backup
goes through `mask_secrets` before it's returned, saved or committed. The rules follow the
Oxidized `itmgos` model (ytti/oxidized#3953) and are tested against the same fixture.
"""

from __future__ import annotations

import re

EXPORT_COMMAND = "admin config commands copypaste"
CONFIRM_PROMPT = r"continue\? \[no\]"
MASK = "<secret hidden>"

# Chatter the export prints around the configuration.
_CHATTER = re.compile(
    r"^(?:The configuration will be output.*|Do you want to continue\?.*|yes|"
    r"One moment while the file is generated.*)$"
)
_PROMPT = re.compile(r"^\[[\w.-]{1,64}\]>\s?$")

# A command line that ends in a secret's name; the CLI prompts for the value and a confirmation.
_PROMPTED_CMD = (
    r"(?:set|admin) (?:[^\n]*(?:password|phrase|secret|key)|site (?:dialoutpassword|"
    r"chapsecret) [^\n]+)"
)

# (name, regex, replacement), applied in order as whole-text MULTILINE substitutions, the same
# way netutils applies Golden Config "config replace" rules, so they can be seeded there as is.
SECRET_RULES: list[tuple[str, str, str]] = [
    (
        "ITMGOS prompted secrets",
        # The command line ends in the secret's name; the value and its confirmation follow
        # on the next two lines: "set tacacs+ secret", "admin ftp password",
        # "set site chapsecret <site>", per-port dial-out and CHAP secrets.
        rf"^({_PROMPTED_CMD}\n)[^\n]*\n[^\n]*\n",
        rf"\1{MASK}\n{MASK}\n",
    ),
    (
        "ITMGOS SNMP communities",
        r"\b((?:ro|rw|trap)community) (?:'[^'\n]*'|\"[^\"\n]*\"|\S+)",
        rf"\1 '{MASK}'",
    ),
    (
        "ITMGOS snmptrap action community",
        r"(\saction snmptrap\b[^\n]*?\scommunity) (?:<secret hidden>|'[^'\n]*'|\"[^\"\n]*\"|\S+)",
        rf"\1 {MASK}",
    ),
    (
        "ITMGOS local user password hashes",
        r"^(set localusers password \S+ enc) (?:<secret hidden>|\S+)",
        rf"\1 {MASK}",
    ),
]

_COMPILED = [(re.compile(regex, re.MULTILINE), repl) for _, regex, repl in SECRET_RULES]


# ESC sequences: OSC (title etc.), CSI, two-character escapes, and nF escapes.
_ANSI = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[ -/]+[0-~]|\x1b[@-Z\\-_]"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # every C0 control except \t and \n, plus DEL
_LINE_ENDS = re.compile(r"\r*\n\r?|\r")
_PROMPTED = re.compile(rf"^{_PROMPTED_CMD}$")
# Looser variant for the guard (with _PROMPTED, via _is_prompted), so a line the mask rule
# skips still fails closed.
_PROMPTED_LOOSE = re.compile(r"^\s*(?:set|admin)\b.*(?:password|phrase|secret|key)\s*$")


# A setting whose name says it holds a secret: "bindpassword", "v3phrase", "chapsecret",
# "rocommunity", "key", and "enc" (the hash that follows a local user's password).
_SECRET_NAME = re.compile(
    r"[A-Za-z0-9_+.-]*(?:password|passphrase|phrase|secret|community|psk|key)", re.I
)
# Values that follow such a name without being a secret: on/off settings like
# "requiresshpublickey disable" and "changepassword enable".
_NOT_A_SECRET = frozenset({"enable", "disable", "enabled", "disabled", "none", "''", '""'})
# A masked value with text stuck to its end: the mask rule stopped short of the whole value.
_MASK_RESIDUE = re.compile(re.escape(MASK) + r"['\"]?[^\s'\"]")


def _names_secret(token: str) -> bool:
    # A whole word, so the mask's own "<secret" isn't taken for a setting.
    return token.lower() == "enc" or bool(_SECRET_NAME.fullmatch(token))


def _is_prompted(line: str) -> bool:
    """The guard's recognizer: everything the mask rule matches, plus looser variants.

    The last variant is any line that ends in a secret's name, whatever its first word, so
    a prompted secret under a command the mask rule doesn't know still fails closed.
    """
    if _PROMPTED.match(line) or _PROMPTED_LOOSE.match(line):
        return True
    tokens = line.split()
    return len(tokens) > 1 and _names_secret(tokens[-1])


class UnmaskedSecretError(ValueError):
    """The export's secrets couldn't be proven masked; nothing may be saved."""


def normalize(output: str) -> str:
    """Strip escape sequences and control characters; fold CR/LF mixes to single LFs.

    Raw channel reads can carry \\r\\r\\n, \\n\\r or terminal escapes. Left in, they shift a
    prompted secret's value off the line the masking rule expects.
    """
    text = _ANSI.sub("", output).replace("\x1b", "")
    return _CONTROL.sub("", _LINE_ENDS.sub("\n", text))


def _prompted_values(lines: list[str]) -> list[str]:
    """Values of prompted secrets, checked for alignment before masking.

    The CLI prints the value and its confirmation on the two lines after the command. Both
    blank means the secret is unset. Anything else that doesn't pair up means the output is
    misaligned, and masking by position could miss the secret, so the export is refused.
    """
    values = []
    for i, line in enumerate(lines):
        if _is_prompted(line):
            pair = lines[i + 1 : i + 3]
            if len(pair) != 2 or pair[0] != pair[1]:
                raise UnmaskedSecretError(
                    f"line {i + 1}: a prompted secret has no matching value and confirmation"
                )
            if pair[0] and pair[0] != MASK:  # MASK: already masked, re-masking is a no-op
                values.append(pair[0])
    return values


def mask_secrets(config: str) -> str:
    values = _prompted_values(config.split("\n"))
    for pattern, repl in _COMPILED:
        config = pattern.sub(repl, config)
    _assert_masked(config, values)
    return config


def _assert_masked(config: str, values: list[str]) -> None:
    lines = config.split("\n")
    for i, line in enumerate(lines):
        if _is_prompted(line) and lines[i + 1 : i + 3] != [MASK, MASK]:
            raise UnmaskedSecretError(
                f"line {i + 1}: a prompted secret isn't followed by two masked lines"
            )
    for value in values:
        # Exact line match for short values (a substring test would flag common words);
        # substring for anything long enough to be distinctive.
        leaked = value in lines if len(value) < 6 else value in config
        if leaked:
            raise UnmaskedSecretError("a prompted secret value is still present after masking")
    _assert_no_inline_secret(lines)


def _assert_no_inline_secret(lines: list[str]) -> None:
    """Refuse anything that looks like a secret the rules above didn't mask.

    The rules know the secret formats of the firmware they were written against. A format
    they don't know must stop the backup, not pass through it: a value on the same line as
    a secret-named setting, a masked value with part of the original left behind, or key
    material. Messages name the line and the setting, never the value.
    """
    for number, line in enumerate(lines, 1):
        if "-----BEGIN" in line:
            raise UnmaskedSecretError(f"line {number}: key or certificate material in the export")
        if _MASK_RESIDUE.search(line):
            raise UnmaskedSecretError(f"line {number}: part of a secret is left after masking")
        tokens = line.split()
        for i, (name, value) in enumerate(zip(tokens, tokens[1:], strict=False)):
            if not _names_secret(name) or _pair_is_safe(line, tokens, i, value):
                continue
            setting = re.sub(r"\W", "", name)[:32]
            raise UnmaskedSecretError(
                f"line {number}: {setting} has a value the masking rules don't cover"
            )


def _pair_is_safe(line: str, tokens: list[str], i: int, value: str) -> bool:
    """True if the value after the secret-named setting at ``tokens[i]`` isn't a live secret.

    Every pair on every line is judged by itself: one masked value on a line says nothing
    about another setting on the same line.
    """
    if value.lower() in _NOT_A_SECRET:
        return True  # an on/off setting
    if (
        value.lstrip("'\"").startswith(MASK.split()[0])
        and tokens[i + 2 : i + 3]
        and tokens[i + 2].startswith(MASK.split()[1])
    ):
        return True  # this pair's own value is the mask
    if tokens[i + 2 : i + 3] == ["enc"]:
        return True  # "password <user> enc <hash>": a user name; the hash is the next pair
    # "set site dialoutpassword <site>": the site's name; the secret is on the next two lines.
    return bool(_PROMPTED.match(line)) and i == 2 and tokens[1] == "site"


def clean_export(output: str) -> str:
    """Raw export (with or without the command echo and trailing prompt) to the masked config."""
    text = normalize(output)
    lines = [
        line for line in text.split("\n") if not _CHATTER.match(line) and not _PROMPT.match(line)
    ]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return mask_secrets("\n".join(lines) + "\n") if lines else ""
