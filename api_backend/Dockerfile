# Dockerfile
# Usa una imagen oficial de Python como base.
FROM python:3.9-slim

# Establece el directorio de trabajo dentro del contenedor.
WORKDIR /app

# Copia el archivo de requerimientos al contenedor.
COPY requirements.txt .

# Instala las dependencias.
# --no-cache-dir para no guardar el caché y hacer la imagen más ligera.
# --trusted-host pypi.python.org para evitar problemas de SSL en algunas redes.
RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

# Copia el resto del código de la aplicación al contenedor.
COPY . .

# Expone el puerto 8080. Cloud Run usará este puerto para recibir tráfico.
EXPOSE 8080

# Comando para ejecutar la aplicación.
# Uvicorn es el servidor que correrá la API de FastAPI.
# --host 0.0.0.0 para que sea accesible desde fuera del contenedor.
# --port 8080 para que coincida con el puerto expuesto.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
