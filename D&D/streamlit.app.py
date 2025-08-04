import streamlit as st
import openai
import time
import re

# --- 1. Konfiguracja strony i stylów ---
st.set_page_config(
    page_title="D&D: Edycja Wizualna",
    page_icon="🐉",
    layout="wide"
)

# Funkcja do wstrzykiwania niestandardowego CSS
def local_css(file_name):
    with open(file_name) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Tworzymy plik CSS (można też go mieć jako osobny plik)
css_style = """
/* Ogólne style dla motywów */
.stApp {
    background-color: var(--background-color);
    color: var(--text-color);
}
.stSidebar {
    background-color: var(--sidebar-bg-color);
}
.stButton>button {
    background-color: var(--button-bg-color);
    color: var(--button-text-color);
    border: 1px solid var(--button-border-color);
}
.stTextInput>div>div>input, .stSelectbox>div>div {
    background-color: var(--widget-bg-color);
}
[data-testid="stChatMessage"] {
    background-color: var(--message-bg-color);
    border-radius: 10px;
    padding: 1rem;
    margin-bottom: 1rem;
}
"""

# Definicje motywów
light_theme = """
<style>
:root {
    --background-color: #FFFFFF;
    --sidebar-bg-color: #F0F2F6;
    --text-color: #31333F;
    --widget-bg-color: #FFFFFF;
    --button-bg-color: #0068C9;
    --button-text-color: #FFFFFF;
    --button-border-color: #0068C9;
    --message-bg-color: #F0F2F6;
}
""" + css_style + "</style>"

dark_theme = """
<style>
:root {
    --background-color: #0E1117;
    --sidebar-bg-color: #1A1D24;
    --text-color: #FAFAFA;
    --widget-bg-color: #262730;
    --button-bg-color: #1E6FBF;
    --button-text-color: #FFFFFF;
    --button-border-color: #1E6FBF;
    --message-bg-color: #262730;
}
""" + css_style + "</style>"


# --- 2. Klucz API OpenAI ---
try:
    openai.api_key = st.secrets["OPENAI_API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("Nie znaleziono klucza API OpenAI. Ustaw go w pliku `.streamlit/secrets.toml`.")
    st.stop()


# --- 3. Inicjalizacja stanu sesji ---
if "messages" not in st.session_state:
    # Zaktualizowany prompt systemowy, proszący o tag [IMG:...]
    st.session_state.messages = [
        {"role": "system", "content": "Jesteś charyzmatycznym Mistrzem Gry prowadzącym kampanię D&D 5e. Opisujesz świat barwnie i szczegółowo. Po każdej swojej odpowiedzi, dodaj na samym końcu specjalny tag `[IMG: ...]` zawierający krótki, ale plastyczny opis sceny w języku angielskim, który posłuży do wygenerowania obrazu. Opis powinien być w stylu 'epic fantasy art, ...'. Przykład: `[IMG: epic fantasy art, a lone warrior standing at the entrance of a glowing cave, mysterious mist swirling at his feet, cinematic lighting, digital painting]`."}
    ]
if "game_started" not in st.session_state:
    st.session_state.game_started = False
if "theme" not in st.session_state:
    st.session_state.theme = "Ciemny"
if "last_image_url" not in st.session_state:
    st.session_state.last_image_url = "https://placehold.co/1024x1024/1A1D24/FAFAFA?text=Czekam+na+Twoj%C4%85+histori%C4%99...&font=raleway"


# --- 4. Funkcje pomocnicze ---
def parse_response(response_text):
    """Parsuje odpowiedź MG, oddzielając tekst narracji od promptu do obrazu."""
    match = re.search(r'\[IMG: (.*?)\]', response_text)
    if match:
        image_prompt = match.group(1)
        narrative = re.sub(r'\[IMG: .*?\]', '', response_text).strip()
        return narrative, image_prompt
    return response_text, None

def generuj_obraz(prompt):
    """Generuje obraz za pomocą DALL-E 3 i zwraca URL."""
    try:
        response = openai.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="standard",
        )
        return response.data[0].url
    except Exception as e:
        print(f"Błąd generowania obrazu: {e}")
        return None

def zapytaj_MG(tresc):
    """Wysyła wiadomość do GPT, odbiera odpowiedź, generuje obraz i zwraca narrację."""
    st.session_state.messages.append({"role": "user", "content": tresc})
    try:
        response = openai.chat.completions.create(
            model="gpt-4-turbo",
            messages=st.session_state.messages,
            temperature=0.9,
            max_tokens=800
        )
        odpowiedz_raw = response.choices[0].message.content
        narracja, image_prompt = parse_response(odpowiedz_raw)

        st.session_state.messages.append({"role": "assistant", "content": narracja})

        # Generowanie obrazu w tle
        if image_prompt:
            with st.spinner("MG maluje Twoją przygodę..."):
                image_url = generuj_obraz(image_prompt)
                if image_url:
                    st.session_state.last_image_url = image_url

        return narracja
    except Exception as e:
        st.error(f"Wystąpił błąd komunikacji z API: {e}")
        st.session_state.messages.pop()
        return None

def stream_narracji(text):
    """Symuluje pisanie na maszynie."""
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.04)

# --- 5. Interfejs użytkownika (GUI) ---

# Wstrzyknięcie wybranego motywu
st.markdown(dark_theme if st.session_state.theme == "Ciemny" else light_theme, unsafe_allow_html=True)

# --- Panel boczny ---
with st.sidebar:
    st.header("🛠️ Panel Gracza")
    st.image("https://i.imgur.com/e2a2h4d.png", width=100) # Dodano małe logo

    st.session_state.theme = st.radio(
        "Wybierz motyw",
        ("Ciemny", "Jasny"),
        horizontal=True
    )
    st.markdown("---")

    st.subheader("👤 Twoja Postać")
    st.text_input("Imię postaci", "Arion", key="character_name")
    st.selectbox("Klasa", ["Wojownik", "Mag", "Łotrzyk", "Kleryk"], key="character_class")
    st.slider("Punkty Życia (HP)", 0, 100, 25, key="character_hp")

    st.markdown("---")
    st.subheader("🎲 Rzut Kością")
    dice_type = st.selectbox("Typ kości", ["k20", "k12", "k10", "k8", "k6", "k4"])
    if st.button(f"Rzuć {dice_type}!"):
        import random
        result = random.randint(1, int(dice_type[1:]))
        st.success(f"Wynik rzutu {dice_type}: **{result}**")

    st.markdown("---")
    if st.button("Rozpocznij Nową Grę", type="primary", use_container_width=True):
        st.session_state.messages = [
            {"role": "system", "content": "Jesteś charyzmatycznym Mistrzem Gry prowadzącym kampanię D&D 5e. Opisujesz świat barwnie i szczegółowo. Po każdej swojej odpowiedzi, dodaj na samym końcu specjalny tag `[IMG: ...]` zawierający krótki, ale plastyczny opis sceny w języku angielskim, który posłuży do wygenerowania obrazu. Opis powinien być w stylu 'epic fantasy art, ...'. Przykład: `[IMG: epic fantasy art, a lone warrior standing at the entrance of a glowing cave, mysterious mist swirling at his feet, cinematic lighting, digital painting]`."}
        ]
        st.session_state.game_started = True
        st.session_state.last_image_url = "https://placehold.co/1024x1024/1A1D24/FAFAFA?text=Czekam+na+Twoj%C4%85+histori%C4%99...&font=raleway"
        initial_prompt = f"Rozpoczynamy nową grę! Moja postać to {st.session_state.character_name}, {st.session_state.character_class}. Opisz świat i wprowadź mnie do przygody."
        zapytaj_MG(initial_prompt)
        st.rerun()

# --- Główny interfejs gry ---
col1, col2 = st.columns([2, 1.3]) # Podział na kolumnę narracji i obrazu

with col1:
    st.header("📜 Kronika Przygody")
    # Kontener na historię czatu, aby chat_input był na dole
    chat_container = st.container()
    with chat_container:
        if not st.session_state.game_started:
             st.info("Witaj w świecie przygód! Użyj panelu po lewej, aby stworzyć postać i rozpocząć grę.")
        else:
            # Wyświetlanie historii czatu
            for message in st.session_state.messages:
                if message["role"] == "system":
                    continue
                avatar = "🧑‍" if message["role"] == "user" else "🐉"
                with st.chat_message(message["role"], avatar=avatar):
                    st.markdown(message["content"])

with col2:
    st.header("🎨 Wizualizacja Sceny")
    st.image(st.session_state.last_image_url, use_column_width=True)
    st.caption("Obraz wygenerowany przez AI na podstawie opisu Mistrza Gry.")


# Pole do wprowadzania akcji gracza
if prompt := st.chat_input("Co robisz dalej?"):
    # Wyświetlenie akcji gracza
    with chat_container:
        with st.chat_message("user", avatar="🧑‍"):
            st.markdown(prompt)

    # Otrzymanie i wyświetlenie odpowiedzi MG
    response = zapytaj_MG(prompt)
    if response:
        # Odświeżenie strony, aby poprawnie wyświetlić nową wiadomość i obraz
        st.rerun()

