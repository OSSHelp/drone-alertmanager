FROM ubuntu:xenial as compile-image
COPY requirements.txt /tmp/
# hadolint ignore=DL3008,DL3015
RUN apt-get -qq update \
 && apt-get -qq install libssl-dev libffi-dev python3-pip >/dev/null \
 && pip3 install -r /tmp/requirements.txt >/dev/null

FROM ubuntu:xenial as runtime-image

COPY --from=compile-image /usr/local /usr/local/
# hadolint ignore=DL3008
RUN apt-get -qq update \
 && apt-get -qq install --no-install-recommends ca-certificates jq bash python3-minimal python3-yaml >/dev/null \
 && rm -rf /tmp/* \
 && rm -rf /var/lib/apt/* \
 && rm -rf /var/log/* \
 && rm -rf /root/.cache
COPY templates/ templates/
COPY docker-entrypoint.py /usr/local/bin/
RUN chmod 744 /usr/local/bin/docker-entrypoint.py
ENV LANG C.UTF-8
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.py"]
