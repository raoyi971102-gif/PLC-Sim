FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPCUASIM_DATA_DIR=/var/lib/opcua-sim \
    OPCUASIM_CSV=/opt/opcua-sim/data/szlab_plc_0810.csv

WORKDIR /opt/opcua-sim

COPY OpcUaSim/ /opt/opcua-sim/
RUN python -m pip install --no-cache-dir . \
    && groupadd --gid 10001 opcua-sim \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/opcua-sim opcua-sim \
    && mkdir -p /var/lib/opcua-sim \
    && chown -R 10001:10001 /var/lib/opcua-sim

USER 10001:10001

EXPOSE 18765 4855

ENTRYPOINT ["opcua-sim"]
CMD ["gui", "--host", "0.0.0.0", "--port", "18765", "--no-open"]

