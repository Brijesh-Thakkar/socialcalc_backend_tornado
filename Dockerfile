FROM python:3.14-slim-bookworm

WORKDIR /app

# Install system dependencies (fonts-noto-core provides Noto Sans Devanagari etc. so
# wkhtmltopdf can render Hindi invoice text instead of dropping the glyphs)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wkhtmltopdf \
        xvfb \
	xauth \
        fonts-noto-core && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Wrapper expected by the Tornado code
RUN printf '#!/bin/bash\nxvfb-run -a wkhtmltopdf "$@"\n' > /usr/local/bin/wkhtmltopdf.sh && \
    chmod +x /usr/local/bin/wkhtmltopdf.sh

ENV S3_USE_SIGV4=True
ENV HTMLTOPDF_BASE=/tmp

EXPOSE 8888

CMD ["python", "cloudmain-dev.py", "--port=8888"]
