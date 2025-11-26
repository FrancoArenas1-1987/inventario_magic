import os
from openai import OpenAI

print("API KEY:", os.getenv("OPENAI_API_KEY"))

client = OpenAI()  # no hace falta pasar api_key si ya está en la variable de entorno
print("Cliente OK")
