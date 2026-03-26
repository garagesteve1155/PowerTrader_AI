FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN apt-get update \
	&& apt-get install -y --no-install-recommends python3-tk \
	&& rm -rf /var/lib/apt/lists/* \
	&& pip install --no-cache-dir -r requirements.txt

COPY . .

# Install lightweight window manager and VNC tools, add a startup script.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends xvfb x11vnc fluxbox x11-utils wmctrl \
	&& rm -rf /var/lib/apt/lists/* \
	&& chmod +x /app/start.sh

ENTRYPOINT ["/app/start.sh"]
CMD ["python", "pt_hub.py"]
