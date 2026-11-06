# Nautobot image with nautobot-ssot and this app, built from this checkout.
# Build context is the repository root; see "Docker" in the README.
FROM networktocode/nautobot:3.2.8-py3.12

USER root
COPY pyproject.toml README.md /tmp/nautobot-lantronix/
COPY nautobot_ssot_lantronix /tmp/nautobot-lantronix/nautobot_ssot_lantronix
RUN pip install --no-cache-dir "nautobot-ssot==4.8.0" /tmp/nautobot-lantronix && rm -rf /tmp/nautobot-lantronix
USER nautobot
