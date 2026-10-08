ARG BASE=archlinux:latest
FROM ${BASE}
COPY deps.sh /deps.sh
RUN sh /deps.sh
RUN useradd -m tester
USER tester
WORKDIR /home/tester
ENV PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8
