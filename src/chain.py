#Chain será quem pega os chunks do retriever, monta um prompt e chama o gemini pra resposta
def resposta(pergunta: str):
    from google import genai
    import os
    from dotenv import load_dotenv
    load_dotenv()
    client = genai.Client()
    response=client.models.generate_content(
        model="gemini-3.5-flash",
        contents=pergunta
    )
    return response.text
