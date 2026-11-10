FROM python:3.13-slim

WORKDIR /app

# install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy the application source
COPY . .

# the commit this image was built from (set by docker.yml); release notes diff it against the
# last one announced. after the copy so it never invalidates the layers above
ARG GIT_SHA=
ENV NOODLE_SHA=$GIT_SHA

# run as a non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "main.py"]
