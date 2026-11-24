FROM python:3.15-slim
# CRAFT_IN_CONTAINER lets the app warn when a storage folder is not mapped (it would fill Docker's own disk).
# PYTHONDONTWRITEBYTECODE keeps Python from writing .pyc files into the image layer (and works with read_only: true).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data CRAFT_IN_CONTAINER=1
# Pick up Debian security fixes released after the base image was built, so a rebuild is enough to get them.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# pip is only needed to install the requirements. Removing it (and the copy ensurepip keeps) also removes the
# libraries pip bundles, such as urllib3, msgpack and setuptools, which vulnerability scanners flag.
RUN pip install -r requirements.txt \
 && pip uninstall -y pip \
 && rm -rf "$(python -c 'import ensurepip, os; print(os.path.dirname(ensurepip.__file__))')/_bundled"
# Nothing here needs setuid or setgid programs (mount, su, passwd...): the entrypoint starts as root only to fix
# /data ownership and then drops privileges with setpriv. Clearing the bits removes a local privilege escalation path.
RUN find / -xdev -type f -perm /6000 -exec chmod a-s {} +
COPY app/ .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir /data
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8000/healthz')"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn","-b","0.0.0.0:8000","--workers","1","--threads","4","--timeout","120","app:app"]
