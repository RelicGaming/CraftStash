FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN useradd -r -u 10001 app && mkdir /data && chown app /data
COPY app/ .
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["gunicorn","-b","0.0.0.0:8000","--workers","1","--threads","4","app:app"]
