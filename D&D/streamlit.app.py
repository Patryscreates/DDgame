import streamlit as st
import openai
import time
import re
from google.cloud import firestore
from google.oauth2 import service_account
import json
import random
import string

# --- 1. Konfiguracja strony ---
st.set_page_config(
    page_title="D&D Multiplayer",
    page_icon="�",
    layout="wide"
)

# --- 2. Połączenie z Firebase ---
@st.cache_resource
def get_db_connection():
    """Pobiera i cachuje połączenie z bazą danych Firestore."""
    try:
        # Używamy sekretów Streamlit do przechowywania danych uwierzytelniających
        creds_json = dict(st.secrets["firebase_credentials"])
        creds = service_account.Credentials.from_service_account_info(creds_json)
        db = firestore.Client(credentials=creds, project=creds_json['project_id'])
        return db
    except Exception as e:
        st.error(f"Nie udało się połączyć z Firebase. Sprawdź swoje sekrety. Błąd: {e}")
        st.stop()

db = get_db_connection()

# --- 3. Konfiguracja OpenAI ---
try:
    openai.api_key = st.secrets["OPENAI_API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("Nie znaleziono klucza API OpenAI. Ustaw go w sekretach Streamlit.")
    st.stop()

# --- 4. Inicjalizacja stanu sesji ---
if "game_id" not in st.session_state:
    st.session_state.game_id = None
if "player_name" not in st.session_state:
    st.session_state.player_name = None
if "character_generated" not in st.session_state:
    st.session_state.character_generated = False


# --- 5. Funkcje pomocnicze ---
def generate_game_id(length=6):
    """Generuje losowy, 6-znakowy kod gry."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def parse_character_sheet(sheet_text):
    """Parsuje tekst z AI do słownika postaci."""
    character = {}
    try:
        lines = sheet_text.strip().split('\n')
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                character[key.strip().lower()] = value.strip()
    except Exception:
        return None # Zwróć None jeśli parsowanie się nie uda
    return character

def stream_text(text):
    """Symuluje pisanie na maszynie."""
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.04)

# --- 6. Logika Gry ---
def create_game():
    """Tworzy nową grę w Firestore."""
    game_id = generate_game_id()
    st.session_state.game_id = game_id
    
    game_ref = db.collection("games").document(game_id)
    game_ref.set({
        "created_at": firestore.SERVER_TIMESTAMP,
        "active": True
    })
    
    # Inicjalizacja wiadomości powitalnej od MG
    system_prompt = "Jesteś charyzmatycznym Mistrzem Gry prowadzącym kampanię D&D 5e dla grupy graczy. Opisujesz świat barwnie, prowadzisz interakcje z postaciami niezależnymi i zarządzasz mechaniką gry. Rozpoczynasz nową kampanię. Przedstaw graczom świat i sytuację początkową, zachęcając ich do przedstawienia swoich postaci."
    messages_ref = game_ref.collection("messages")
    messages_ref.add({
        "role": "assistant",
        "content": "Witajcie, śmiałkowie, w świecie pełnym magii i niebezpieczeństw! Wasza przygoda wkrótce się rozpocznie. Czekam, aż wszyscy dołączycie i stworzycie swoje postacie...",
        "timestamp": firestore.SERVER_TIMESTAMP,
        "player_name": "Mistrz Gry"
    })
    st.rerun()

def join_game(game_id, player_name):
    """Dołącza gracza do istniejącej gry."""
    game_ref = db.collection("games").document(game_id).get()
    if game_ref.exists:
        st.session_state.game_id = game_id
        st.session_state.player_name = player_name
        
        # Sprawdź, czy gracz ma już postać
        player_ref = db.collection("games").document(game_id).collection("players").document(player_name).get()
        if player_ref.exists:
            st.session_state.character_generated = True
        
        st.rerun()
    else:
        st.error("Gra o podanym ID nie istnieje.")

def generate_character(concept):
    """Generuje postać za pomocą AI i zapisuje ją w Firestore."""
    with st.spinner("AI tworzy Twoją postać..."):
        try:
            prompt = f"""
            Jesteś kreatorem postaci do gry D&D 5e. Na podstawie poniższego konceptu stwórz unikalną postać.
            Odpowiedz MUSI być w formacie klucz: wartość, każda para w nowej linii. Użyj polskich nazw.
            Klucze to: Imię, Klasa, Rasa, Punkty Życia, Historia.

            Koncept: "{concept}"
            """
            response = openai.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
            )
            character_sheet_text = response.choices[0].message.content
            character_data = parse_character_sheet(character_sheet_text)

            if character_data and all(k in character_data for k in ['imię', 'klasa', 'rasa', 'punkty życia', 'historia']):
                player_ref = db.collection("games").document(st.session_state.game_id).collection("players").document(st.session_state.player_name)
                player_ref.set(character_data)
                st.session_state.character_generated = True
                
                # Poinformuj innych graczy o dołączeniu nowej postaci
                game_ref = db.collection("games").document(st.session_state.game_id)
                messages_ref = game_ref.collection("messages")
                messages_ref.add({
                    "role": "assistant",
                    "content": f"Do drużyny dołącza nowa postać! Przedstawcie się: {character_data['imię']}, {character_data['rasa']} {character_data['klasa']}.",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "player_name": "Mistrz Gry"
                })

                st.rerun()
            else:
                st.error("Nie udało się poprawnie wygenerować postaci. Spróbuj ponownie z innym opisem.")
                st.write("Otrzymano od AI:", character_sheet_text)

        except Exception as e:
            st.error(f"Wystąpił błąd podczas generowania postaci: {e}")

def send_message(content):
    """Wysyła wiadomość gracza i pobiera odpowiedź od MG."""
    game_ref = db.collection("games").document(st.session_state.game_id)
    messages_ref = game_ref.collection("messages")

    # Zapisz wiadomość gracza
    messages_ref.add({
        "role": "user",
        "content": content,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "player_name": st.session_state.player_name
    })

    # Przygotuj historię dla AI
    with st.spinner("Mistrz Gry myśli..."):
        history_query = messages_ref.order_by("timestamp", direction=firestore.Query.ASCENDING).limit(20)
        history_docs = history_query.stream()
        
        messages_for_ai = [{"role": "system", "content": "Jesteś Mistrzem Gry D&D dla grupy graczy. Ich imiona i akcje będą poprzedzone ich nazwą. Prowadź spójną narrację dla całej grupy."}]
        for doc in history_docs:
            msg = doc.to_dict()
            # Dla AI, wiadomości od użytkowników powinny być w formacie "NazwaGracza: Treść"
            ai_content = f"{msg['player_name']}: {msg['content']}" if msg['role'] == 'user' else msg['content']
            messages_for_ai.append({"role": msg['role'], "content": ai_content})
        
        try:
            response = openai.chat.completions.create(
                model="gpt-4-turbo",
                messages=messages_for_ai,
                temperature=0.9
            )
            dm_response = response.choices[0].message.content

            # Zapisz odpowiedź MG
            messages_ref.add({
                "role": "assistant",
                "content": dm_response,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "player_name": "Mistrz Gry"
            })
        except Exception as e:
            st.error(f"Błąd komunikacji z OpenAI: {e}")


# --- 7. Interfejs Użytkownika (GUI) ---

# --- Ekran startowy (Lobby) ---
if not st.session_state.game_id:
    st.title("🎲 Witaj w Multiplayer D&D!")
    st.image("https://images.unsplash.com/photo-1608889353459-b4675451b6a2?q=80&w=2670&auto=format&fit=crop", use_column_width=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Stwórz Nową Grę")
        if st.button("Stwórz Grę", use_container_width=True, type="primary"):
            create_game()

    with col2:
        st.subheader("Dołącz do Gry")
        join_id = st.text_input("Wpisz ID Gry", max_chars=6)
        player_name_join = st.text_input("Wpisz swoje imię (gracza)")
        if st.button("Dołącz", use_container_width=True):
            if join_id and player_name_join:
                join_game(join_id.upper(), player_name_join)
            else:
                st.warning("Wypełnij oba pola, aby dołączyć.")
    st.stop()


# --- Ekran Generowania Postaci ---
if st.session_state.game_id and not st.session_state.character_generated:
    st.title(f"Witaj, {st.session_state.player_name}!")
    st.header("Stwórz swoją postać")
    st.info("Opisz w kilku słowach, kim chcesz być. AI zajmie się resztą!")
    
    character_concept = st.text_area("Np. 'Mroczny elf skrytobójca z dwoma sztyletami' lub 'Dobroduszny niziołek, który uwielbia gotować i leczyć rany'")
    if st.button("Generuj Postać", type="primary"):
        if character_concept:
            generate_character(character_concept)
        else:
            st.warning("Opisz swoją postać, aby ją stworzyć.")
    st.stop()


# --- Główny Ekran Gry ---
st.sidebar.title("Panel Gry")
st.sidebar.markdown(f"**ID Gry:** `{st.session_state.game_id}`")
st.sidebar.markdown(f"**Jesteś zalogowany jako:** `{st.session_state.player_name}`")
st.sidebar.markdown("---")

# Wyświetlanie graczy i ich postaci
st.sidebar.subheader("Drużyna")
players_ref = db.collection("games").document(st.session_state.game_id).collection("players").stream()
for player_doc in players_ref:
    player_data = player_doc.to_dict()
    with st.sidebar.expander(f"**{player_doc.id}** - {player_data.get('imię', 'Brak imienia')}"):
        st.write(f"**Klasa:** {player_data.get('klasa', '?')}")
        st.write(f"**Rasa:** {player_data.get('rasa', '?')}")
        st.write(f"**HP:** {player_data.get('punkty życia', '?')}")
        st.write(f"**Historia:** {player_data.get('historia', '?')}")


# Główny interfejs czatu
st.title("📜 Kronika Przygody")

# Pobieranie i wyświetlanie wiadomości
messages_query = db.collection("games").document(st.session_state.game_id).collection("messages").order_by("timestamp", direction=firestore.Query.ASCENDING)
message_docs = messages_query.stream()

chat_container = st.container()
with chat_container:
    for doc in message_docs:
        msg = doc.to_dict()
        # --- POCZĄTEK ZMIANY ---
        # Sprawdzamy, czy klucz 'role' istnieje w wiadomości, aby uniknąć błędów
        if 'role' in msg and msg['role'] in ['user', 'assistant']:
            # Usuwamy ręczne ustawianie awatara i pozwalamy Streamlit użyć domyślnych
            with st.chat_message(msg['role']):
                # Używamy .get() dla bezpieczeństwa, na wypadek braku danych w bazie
                st.write(f"**{msg.get('player_name', 'Nieznany gracz')}**")
                st.markdown(msg.get('content', '*pusta wiadomość*'))
        # --- KONIEC ZMIANY ---

# Pole do wprowadzania akcji gracza
if prompt := st.chat_input("Co robisz dalej?"):
    send_message(prompt)
    st.rerun() # Odśwież, aby zobaczyć nową wiadomość i odpowiedź MG

# Automatyczne odświeżanie co 15 sekund, aby zobaczyć wiadomości innych graczy
time.sleep(15)
st.rerun()
�
