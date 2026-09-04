FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# The Docker CLI is here for the *embedded* engine, which runs connector images
# on a host daemon. Production uses ENGINE_TYPE=AIRBYTE_API and never touches
# Docker; the socket is mounted only by docker-compose.embedded.yml. curl/gnupg
# are used solely to add Docker's apt repo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && chmod a+r /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Whether this image can run dbt.  `1` (the default) installs the Transform
# runtime; `0` leaves it out and saves roughly 560 MB -- dbt-core drags in
# pandas, pyarrow, numpy and the google-cloud stack, two thirds of the
# installed packages, for a module a small deployment may not use.
#
# Nothing in `app/` imports dbt: the runtime executes the `dbt` binary as a
# subprocess. So a slim image runs every other part of AppBI unchanged, and the
# Transform endpoints answer with a clear error rather than crashing.
ARG WITH_TRANSFORM=1

COPY requirements.txt requirements-transform.txt ./
RUN pip install --default-timeout=120 --retries=10 -r requirements.txt \
 && if [ "$WITH_TRANSFORM" = "1" ]; then \
      pip install --default-timeout=120 --retries=10 -r requirements-transform.txt; \
    fi

# Read at runtime by the API, so a slim image can say so instead of failing
# when somebody opens the Transform screen.
ENV TRANSFORM_RUNTIME_AVAILABLE=${WITH_TRANSFORM}

# The test suite is deliberately not in the runtime image. It used to be, and
# an image that carries its own tests ships code nobody reviewed as product
# code, on a machine that runs customer data. The contract suite needs a live
# engine, so CI mounts `qa/backend` into the container for that one run
# instead.
COPY app ./app
# Schema migrations ship with the code that expects them, so an image can never
# be deployed against a database it does not know how to bring up to date.
COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /engine/workspace /engine/logs /transform/workspace /transform/logs

# Build identity, passed by the release pipeline. The defaults are honest
# about not knowing: a build with BUILD_SHA=unknown cannot produce release
# evidence, which is the right behaviour for an ad-hoc local build.
#
# Declared last on purpose. BUILD_TIME changes on every invocation, and an
# ARG invalidates every layer beneath it -- with this block at the top of
# the file, each build reinstalled the apt packages and the ~1.5 GB of
# Python dependencies from scratch, turning a source-only change into a
# ten-minute rebuild. Nothing below consumes them at build time; they are
# runtime metadata, so the cheapest place for them is the final layer.
ARG BUILD_SHA=unknown
ARG BUILD_DIGEST=""
ARG BUILD_TIME=""
ENV BUILD_SHA=${BUILD_SHA} \
    BUILD_DIGEST=${BUILD_DIGEST} \
    BUILD_TIME=${BUILD_TIME}

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
