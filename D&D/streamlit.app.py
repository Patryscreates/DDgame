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
    page_title="D&D Multiplayer: Edycja Wizualna",
    page_icon="✨",
    layout="wide"
)

# --- 2. Połączenie z Firebase ---
@st.cache_resource
def get_db_connection():
    try:
        creds_json = dict(st.secrets["firebase_credentials"])
        creds = service_account.Credentials.from_service_account_info(creds_json)
        db = firestore.Client(credentials=creds, project=creds_json['project_id'])
        return db
    except Exception as e:
        st.error(f"Błąd połączenia z Firebase: {e}")
        st.stop()

db = get_db_connection()

# --- 3. Konfiguracja OpenAI ---
try:
    openai.api_key = st.secrets["OPENAI_API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("Brak klucza API OpenAI. Ustaw go w sekretach Streamlit.")
    st.stop()

# --- 4. Inicjalizacja stanu sesji ---
for key in ["player_name", "character_exists", "game_id"]:
    if key not in st.session_state:
        st.session_state[key] = None

# --- 5. Funkcje pomocnicze ---
def generate_game_id(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def parse_character_sheet(sheet_text):
    character = {}
    try:
        for line in sheet_text.strip().split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                character[key.strip().lower().replace(" ", "_")] = value.strip()
        portrait_match = re.search(r'\[PORTRET: (.*?)\]', sheet_text, re.IGNORECASE)
        if portrait_match:
            character['portrait_prompt'] = portrait_match.group(1)
    except Exception:
        return None
    return character

@st.cache_data(ttl=3600)
def generate_image(prompt, size="1024x1024"):
    try:
        response = openai.images.generate(model="dall-e-3", prompt=f"digital painting, {prompt}", n=1, size=size, quality="standard")
        return response.data[0].url
    except Exception as e:
        print(f"Błąd generowania obrazu: {e}")
        return None

def update_player_hp(game_id, player_name, new_hp):
    player_ref = db.collection("games").document(game_id).collection("players").document(player_name)
    player_ref.update({"current_hp": str(new_hp)})

# --- 6. Logika Gry ---
def create_game():
    game_id = generate_game_id()
    game_ref = db.collection("games").document(game_id)
    game_ref.set({
        "created_at": firestore.SERVER_TIMESTAMP,
        "active": True,
        "is_typing": None,
        "scene_image_url": "https://placehold.co/1024x1024/0E1117/FFFFFF?text=Przygoda+si%C4%99+zaczyna...&font=raleway"
    })
    
    # Po stworzeniu gry, od razu do niej dołącz
    join_game(game_id)

def join_game(game_id):
    game_ref = db.collection("games").document(game_id)
    if not game_ref.get().exists:
        st.error("Gra o podanym ID nie istnieje.")
        return

    # Pobierz globalną postać i zapisz ją w grze z pełnym HP
    player_global_ref = db.collection("players").document(st.session_state.player_name).get()
    if player_global_ref.exists:
        player_data = player_global_ref.to_dict()
        game_player_ref = game_ref.collection("players").document(st.session_state.player_name)
        game_player_ref.set({
            "current_hp": player_data.get("punkty_życia", "100"), # Start z pełnym HP
            "joined_at": firestore.SERVER_TIMESTAMP
        })
        st.session_state.game_id = game_id
        st.rerun()
    else:
        st.error("Nie znaleziono Twojej postaci. Stwórz ją najpierw.")

def generate_character(concept):
    with st.spinner("AI tworzy Twoją postać..."):
        try:
            prompt = f"""
            Jesteś kreatorem postaci do gry D&D 5e. Na podstawie konceptu stwórz unikalną postać.
            Odpowiedz MUSI być w formacie klucz: wartość, każda para w nowej linii. Użyj polskich nazw.
            Klucze to: Imię, Klasa, Rasa, Punkty Życia, Historia.
            Na samym końcu dodaj tag [PORTRET: ...], a w nim krótki opis wyglądu postaci do wygenerowania portretu, po angielsku, w stylu "fantasy character portrait".
            Przykład: [PORTRET: fantasy character portrait, a cunning male elf rogue with silver hair and glowing green eyes, wearing a dark leather hood]
            Koncept: "{concept}"
            """
            response = openai.chat.completions.create(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], temperature=0.8)
            sheet_text = response.choices[0].message.content
            char_data = parse_character_sheet(sheet_text)

            if char_data and all(k in char_data for k in ['imię', 'klasa', 'rasa', 'punkty_życia', 'historia', 'portrait_prompt']):
                with st.spinner("AI maluje portret Twojej postaci..."):
                    portrait_url = generate_image(char_data['portrait_prompt'], size="1024x1024")
                    char_data['portrait_url'] = portrait_url or "https://placehold.co/512x512/333/FFF?text=Brak+Portretu"
                
                # Zapisz postać w globalnej kolekcji 'players'
                player_ref = db.collection("players").document(st.session_state.player_name)
                player_ref.set(char_data)
                st.session_state.character_exists = True
                st.rerun()
            else:
                st.error("Nie udało się poprawnie wygenerować postaci. Spróbuj ponownie.")
                st.write("Otrzymano od AI:", sheet_text)
        except Exception as e:
            st.error(f"Wystąpił błąd podczas generowania postaci: {e}")

def send_message(content, is_action=True):
    game_ref = db.collection("games").document(st.session_state.game_id)
    messages_ref = game_ref.collection("messages")

    game_ref.update({"is_typing": st.session_state.player_name})
    messages_ref.add({"role": "user", "content": content, "timestamp": firestore.SERVER_TIMESTAMP, "player_name": st.session_state.player_name})

    if not is_action:
        game_ref.update({"is_typing": None})
        return

    with st.spinner("Mistrz Gry myśli..."):
        game_ref.update({"is_typing": "Mistrz Gry"})
        history_query = messages_ref.order_by("timestamp", direction=firestore.Query.ASCENDING).limit(20)
        
        system_prompt = "Jesteś Mistrzem Gry D&D. Prowadź narrację dla grupy. Po każdej swojej odpowiedzi, dodaj na samym końcu tag `[IMG: ...]` z opisem sceny po angielsku, w stylu 'epic fantasy art, ...'."
        messages_for_ai = [{"role": "system", "content": system_prompt}]
        for doc in history_query.stream():
            msg = doc.to_dict()
            ai_content = f"{msg.get('player_name', '')}: {msg.get('content', '')}" if msg.get('role') == 'user' else msg.get('content', '')
            messages_for_ai.append({"role": msg.get('role', 'user'), "content": ai_content})
        
        try:
            response = openai.chat.completions.create(model="gpt-4-turbo", messages=messages_for_ai, temperature=0.9)
            dm_response_raw = response.choices[0].message.content
            narrative, img_prompt = (re.match(r"(.*)\[IMG: (.*?)\]", dm_response_raw, re.DOTALL).groups()) if re.search(r'\[IMG: .*?\]', dm_response_raw) else (dm_response_raw, None)
            messages_ref.add({"role": "assistant", "content": narrative.strip(), "timestamp": firestore.SERVER_TIMESTAMP, "player_name": "Mistrz Gry"})
            if img_prompt:
                with st.spinner("MG maluje scenę..."):
                    scene_url = generate_image(img_prompt)
                    if scene_url: game_ref.update({"scene_image_url": scene_url})
        except Exception as e:
            st.error(f"Błąd komunikacji z OpenAI: {e}")
        finally:
            game_ref.update({"is_typing": None})

# --- 7. Interfejs Użytkownika (GUI) ---

# --- Ekran logowania gracza ---
if not st.session_state.player_name:
    st.title("✨ Witaj w Świecie Przygód D&D ✨")
    st.header("Przedstaw się, aby rozpocząć")
    player_name_input = st.text_input("Wpisz swoje imię (będzie to Twój unikalny login)", key="player_login")
    if st.button("Zaloguj się", use_container_width=True, type="primary"):
        if player_name_input:
            st.session_state.player_name = player_name_input
            player_ref = db.collection("players").document(player_name_input).get()
            if player_ref.exists:
                st.session_state.character_exists = True
            st.rerun()
        else:
            st.warning("Podaj swoje imię.")
    st.stop()

# --- Ekran tworzenia postaci (jeśli nie istnieje) ---
if st.session_state.player_name and not st.session_state.character_exists:
    st.title(f"Witaj, {st.session_state.player_name}!")
    st.header("Stwórz swoją pierwszą postać")
    col1, col2 = st.columns([2, 1])
    with col1:
        st.info("Opisz w kilku słowach, kim chcesz być. AI zajmie się resztą!")
        character_concept = st.text_area("Np. 'Mroczny elf skrytobójca z dwoma sztyletami'", height=150)
        if st.button("Generuj Postać wg opisu", type="primary", use_container_width=True):
            if character_concept: generate_character(character_concept)
            else: st.warning("Opisz swoją postać.")
    with col2:
        st.info("...albo zdaj się na los!")
        if st.button("Losuj Postać!", use_container_width=True):
            concepts = ["a brave dwarven warrior with a giant axe", "a wise old human wizard", "a sneaky halfling rogue", "a noble elf paladin", "a chaotic gnome artificer"]
            generate_character(random.choice(concepts))
    st.stop()

# --- Lobby Gier (po zalogowaniu i stworzeniu postaci) ---
if st.session_state.player_name and st.session_state.character_exists and not st.session_state.game_id:
    st.title(f"Witaj z powrotem, {st.session_state.player_name}!")
    st.header("Wybierz swoją przygodę")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Stwórz Nową Grę")
        if st.button("Stwórz Grę", use_container_width=True, type="primary"):
            create_game()
    with col2:
        st.subheader("Dołącz do Gry")
        join_id = st.text_input("Wpisz ID Gry", max_chars=6)
        if st.button("Dołącz do Gry", use_container_width=True):
            if join_id: join_game(join_id.upper())
            else: st.warning("Wpisz ID gry, aby dołączyć.")
    st.stop()


# --- Główny Ekran Gry ---
game_doc_ref = db.collection("games").document(st.session_state.game_id)
game_data = game_doc_ref.get().to_dict()
is_typing_by = game_data.get("is_typing")

st.sidebar.title("Panel Gry")
st.sidebar.markdown(f"**ID Gry:** `{st.session_state.game_id}`")
st.sidebar.markdown(f"**Jesteś zalogowany jako:** `{st.session_state.player_name}`")
st.sidebar.markdown("---")

st.sidebar.subheader("Drużyna")
game_players_ref = game_doc_ref.collection("players").stream()
for game_player_doc in game_players_ref:
    player_name = game_player_doc.id
    game_player_data = game_player_doc.to_dict()
    
    # Pobierz globalne dane postaci
    player_global_data = db.collection("players").document(player_name).get().to_dict() or {}

    with st.sidebar.expander(f"**{player_name}** - {player_global_data.get('imię', 'Brak imienia')}"):
        st.image(player_global_data.get('portrait_url', ''), use_column_width=True)
        st.write(f"**Klasa:** {player_global_data.get('klasa', '?')}")
        st.write(f"**Rasa:** {player_global_data.get('rasa', '?')}")
        
        # Edytowalne HP (zapisywane w grze)
        hp_key = f"hp_{player_name}_{st.session_state.game_id}"
        current_hp = int(game_player_data.get('current_hp', 0))
        new_hp = st.number_input("Punkty Życia", value=current_hp, key=hp_key, step=1)
        if new_hp != current_hp:
            update_player_hp(st.session_state.game_id, player_name, new_hp)
            st.toast(f"Zaktualizowano HP dla {player_global_data.get('imię', '')}!")

        st.write(f"**Historia:** {player_global_data.get('historia', '?')}")

st.sidebar.markdown("---")
st.sidebar.subheader("🎲 Rzut Kością")
dice_type = st.sidebar.selectbox("Typ kości", ["k20", "k12", "k10", "k8", "k6", "k4"])
if st.sidebar.button(f"Rzuć {dice_type}!"):
    result = random.randint(1, int(dice_type[1:]))
    dice_roll_content = f"Rzucam kością {dice_type} i wyrzucam **{result}**."
    send_message(dice_roll_content, is_action=False)
    st.rerun()

col1, col2 = st.columns([2, 1])
with col1:
    st.header("📜 Kronika Przygody")
    messages_query = game_doc_ref.collection("messages").order_by("timestamp", direction=firestore.Query.ASCENDING)
    
    chat_container = st.container()
    with chat_container:
        for doc in messages_query.stream():
            msg = doc.to_dict()
            if 'role' in msg and msg['role'] in ['user', 'assistant']:
                with st.chat_message(msg['role']):
                    st.write(f"**{msg.get('player_name', 'Nieznany gracz')}**")
                    st.markdown(msg.get('content', '*pusta wiadomość*'))
with col2:
    st.header("🎨 Wizualizacja Sceny")
    st.image(game_data.get("scene_image_url", ""), use_column_width=True)
    st.caption("Obraz wygenerowany przez AI na podstawie opisu Mistrza Gry.")

input_disabled = is_typing_by is not None
placeholder_text = "Co robisz dalej?"
if is_typing_by:
    placeholder_text = f"{is_typing_by} wykonuje ruch... Poczekaj na swoją kolej."

if prompt := st.chat_input(placeholder_text, disabled=input_disabled):
    send_message(prompt)
    st.rerun()

time.sleep(10)
st.rerun()
