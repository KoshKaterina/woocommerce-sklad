FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --upgrade pip==26.2.2 \
    && pip install --no-cache-dir -r requirements.lock \
    && pip check

COPY woo_moysklad/ woo_moysklad/

ENV WOO_LOG_FILE=/tmp/woo_moysklad.log PYTHONDONTWRITEBYTECODE=1
USER 65532:65532

EXPOSE 8000

CMD ["uvicorn", "woo_moysklad.main:app", "--host", "0.0.0.0", "--port", "8000"]
