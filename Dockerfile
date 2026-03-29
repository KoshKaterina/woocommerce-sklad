FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY woo_moysklad/ woo_moysklad/

EXPOSE 8000

CMD ["uvicorn", "woo_moysklad.main:app", "--host", "0.0.0.0", "--port", "8000"]
