ARG PYTHON_BASE
ARG NODE_BASE
FROM ${NODE_BASE} AS node
FROM ${PYTHON_BASE} AS build
ARG DEBIAN_SNAPSHOT
ARG TESSERACT_COMMIT
ARG TESSERACT_ARCHIVE_SHA256
ARG TESSDATA_COMMIT
ARG ENG_SHA256
ARG OSD_SHA256
RUN printf 'Types: deb\nURIs: https://snapshot.debian.org/archive/debian/%s/\nSuites: bookworm bookworm-updates\nComponents: main\nCheck-Valid-Until: no\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: https://snapshot.debian.org/archive/debian-security/%s/\nSuites: bookworm-security\nComponents: main\nCheck-Valid-Until: no\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends build-essential pkg-config autoconf automake libtool libleptonica-dev libarchive-dev ca-certificates curl
RUN curl --fail --location --retry 3 "https://codeload.github.com/tesseract-ocr/tesseract/tar.gz/$TESSERACT_COMMIT" --output /tmp/tesseract.tar.gz \
    && echo "$TESSERACT_ARCHIVE_SHA256  /tmp/tesseract.tar.gz" | sha256sum --check \
    && mkdir /tmp/tesseract \
    && tar --extract --gzip --file /tmp/tesseract.tar.gz --strip-components=1 --directory /tmp/tesseract \
    && cd /tmp/tesseract && ./autogen.sh && ./configure --disable-static --disable-openmp \
    && make -j2 && make install \
    && mkdir -p /usr/local/share/tessdata \
    && curl --fail --location --retry 3 "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$TESSDATA_COMMIT/eng.traineddata" --output /usr/local/share/tessdata/eng.traineddata \
    && curl --fail --location --retry 3 "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$TESSDATA_COMMIT/osd.traineddata" --output /usr/local/share/tessdata/osd.traineddata \
    && echo "$ENG_SHA256  /usr/local/share/tessdata/eng.traineddata" | sha256sum --check \
    && echo "$OSD_SHA256  /usr/local/share/tessdata/osd.traineddata" | sha256sum --check

FROM ${PYTHON_BASE}
ARG DEBIAN_SNAPSHOT
RUN printf 'Types: deb\nURIs: https://snapshot.debian.org/archive/debian/%s/\nSuites: bookworm bookworm-updates\nComponents: main\nCheck-Valid-Until: no\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: https://snapshot.debian.org/archive/debian-security/%s/\nSuites: bookworm-security\nComponents: main\nCheck-Valid-Until: no\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends git liblept5 libarchive13 libgl1 libglib2.0-0 libgomp1 ca-certificates fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bin/tesseract /usr/local/bin/tesseract
COPY --from=build /usr/local/lib/libtesseract.so* /usr/local/lib/
COPY --from=build /usr/local/share/tessdata /usr/local/share/tessdata
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ldconfig \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY backend/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock
ENV TESSDATA_PREFIX=/usr/local/share/tessdata OMP_THREAD_LIMIT=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
