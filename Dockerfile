# Copyright (c) Datalayer, Inc. https://clouder.io
# Distributed under the terms of the MIT License.

FROM python:3.10.4

RUN mkdir /opt/clouder

WORKDIR /opt/clouder

RUN pip install kazoo

COPY backplane /opt/clouder/backplane
RUN pip install -e ./backplane

COPY frontplane/dist.html /opt/clouder/index.html

WORKDIR /opt/clouder/editor

EXPOSE 9300

CMD ["python", "clouder/main.py"]
