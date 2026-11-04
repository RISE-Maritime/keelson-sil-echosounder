# keelson-sil-echosounder: a simulated echo sounder.
#
# A package and its dependencies and nothing else. keelson installs from PyPI
# at the version pinned in pyproject.toml, never a local checkout.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY keelson_sil_echosounder ./keelson_sil_echosounder

RUN pip install --no-cache-dir .

ENTRYPOINT ["keelson-sil-echosounder"]
