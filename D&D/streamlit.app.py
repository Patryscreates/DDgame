import streamlit as st
import openai
import time
import re
from google.cloud import firestore
from google.oauth2 import service_account
import json
import random
import string
import streamlit.components.v1 as components
import base64

# --- 1. Konfiguracja strony ---
st.set_page_config(
    page_title="D&D Multiplayer: Edycja Sztos",
    page_icon="🔥",
    layout="wide"
)

# --- Słownik teł wideo ---
BACKGROUNDS = {
    "karczma": "https://cdn.pixabay.com/video/2023/10/05/184813-873138840_large.mp4",
    "las": "https://cdn.pixabay.com/video/2024/02/09/199582-911442728_large.mp4",
    "jaskinia": "https://cdn.pixabay.com/video/2022/09/13/130113-750107243_large.mp4",
    "zamek": "https://cdn.pixabay.com/video/2023/09/10/182104-864731002_large.mp4",
    "miasto": "https://cdn.pixabay.com/video/2023/09/14/182410-865612402_large.mp4",
    "default": "https://cdn.pixabay.com/video/2023/06/20/170942-838735238_large.mp4"
}

def set_background_video(keyword):
    """Ustawia dynamiczne, animowane tło aplikacji w sposób niezawodny."""
    video_url = BACKGROUNDS.get(keyword, BACKGROUNDS["default"])
    
    video_html = f"""
    <style>
    .stApp {{
        background: #000;
    }}
    #bg-video {{
        position: fixed;
        right: 0;
        bottom: 0;
        min-width: 100%; 
        min-height: 100%;
        z-index: -1;
        filter: brightness(0.4);
    }}
    [data-testid="stSidebar"], .main .block-container {{
        background-color: rgba(14, 17, 23, 0.85);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        padding: 1rem;
    }}
    </style>
    <video autoplay loop muted playsinline id="bg-video">
        <source src="{video_url}" type="video/mp4">
    </video>
    """
    st.markdown(video_html, unsafe_allow_html=True)


def play_dice_sound():
    """Odtwarza dźwięk rzutu kością za pomocą JS."""
    sound_js = """
    <script src="https://cdnjs.cloudflare.com/ajax/libs/tone/14.8.49/Tone.js"></script>
    <script>
        const synth = new Tone.Synth().toDestination();
        synth.triggerAttackRelease("C5", "16n", Tone.now());
        synth.triggerAttackRelease("G4", "16n", Tone.now() + 0.1);
    </script>
    """
    components.html(sound_js, height=0)

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

def parse_response_from_dm(text):
    narrative, img_prompt, bg_keyword, map_prompt = text, None, None, None
    
    img_match = re.search(r'\[IMG: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if img_match:
        img_prompt = img_match.group(1).strip()
        narrative = re.sub(r'\[IMG: .*?\]', '', narrative, flags=re.DOTALL | re.IGNORECASE)

    bg_match = re.search(r'\[TLO: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if bg_match:
        bg_keyword = bg_match.group(1).strip().lower()
        narrative = re.sub(r'\[TLO: .*?\]', '', narrative, flags=re.DOTALL | re.IGNORECASE)
        
    map_match = re.search(r'\[MAPA: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if map_match:
        map_prompt = map_match.group(1).strip()
        narrative = re.sub(r'\[MAPA: .*?\]', '', narrative, flags=re.DOTALL | re.IGNORECASE)

    return narrative.strip(), img_prompt, bg_keyword, map_prompt

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
        "active": True, "is_typing": None,
        "scene_image_url": "https://placehold.co/1024x1024/0E1117/FFFFFF?text=Przygoda+si%C4%99+zaczyna...&font=raleway",
        "map_image_url": "https://placehold.co/1024x1024/0E1117/FFFFFF?text=Mapa+niezbadanych+krain...&font=raleway",
        "background_keyword": "default"
    })
    join_game(game_id)

def join_game(game_id):
    game_ref = db.collection("games").document(game_id)
    if not game_ref.get().exists:
        st.error("Gra o podanym ID nie istnieje.")
        return
    player_global_ref = db.collection("players").document(st.session_state.player_name).get()
    if player_global_ref.exists:
        player_data = player_global_ref.to_dict()
        game_player_ref = game_ref.collection("players").document(st.session_state.player_name)
        game_player_ref.set({"current_hp": player_data.get("punkty_życia", "100"), "joined_at": firestore.SERVER_TIMESTAMP})
        st.session_state.game_id = game_id
        st.rerun()

def generate_character(concept):
    with st.spinner("AI tworzy Twoją postać..."):
        try:
            prompt = f"Stwórz postać D&D na podstawie konceptu: '{concept}'. Format: Imię, Klasa, Rasa, Punkty Życia, Historia. Na końcu dodaj tag [PORTRET: opis po angielsku]."
            response = openai.chat.completions.create(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], temperature=0.8)
            sheet_text = response.choices[0].message.content
            char_data = parse_character_sheet(sheet_text)
            if char_data and all(k in char_data for k in ['imię', 'klasa', 'rasa', 'punkty_życia', 'historia', 'portrait_prompt']):
                with st.spinner("AI maluje portret..."):
                    portrait_url = generate_image(char_data['portrait_prompt'])
                    char_data['portrait_url'] = portrait_url or "https://placehold.co/512x512/333/FFF?text=Brak+Portretu"
                db.collection("players").document(st.session_state.player_name).set(char_data)
                st.session_state.character_exists = True
                st.rerun()
            else:
                st.error("Nie udało się wygenerować postaci. Spróbuj ponownie.")
        except Exception as e:
            st.error(f"Wystąpił błąd: {e}")

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
        
        system_prompt = "Jesteś Mistrzem Gry D&D. Prowadź narrację. Po każdej odpowiedzi dodaj tag `[IMG: opis sceny po angielsku]`, `[TLO: jedno słowo kluczowe lokacji]`. Jeśli to początek przygody, dodaj też tag `[MAPA: opis mapy świata w stylu fantasy]`."
        messages_for_ai = [{"role": "system", "content": system_prompt}]
        for doc in history_query.stream():
            msg = doc.to_dict()
            ai_content = f"{msg.get('player_name', '')}: {msg.get('content', '')}" if msg.get('role') == 'user' else msg.get('content', '')
            messages_for_ai.append({"role": msg.get('role', 'user'), "content": ai_content})
        try:
            response = openai.chat.completions.create(model="gpt-4-turbo", messages=messages_for_ai, temperature=0.9)
            dm_response_raw = response.choices[0].message.content
            narrative, img_prompt, bg_keyword, map_prompt = parse_response_from_dm(dm_response_raw)
            messages_ref.add({"role": "assistant", "content": narrative, "timestamp": firestore.SERVER_TIMESTAMP, "player_name": "Mistrz Gry"})
            if bg_keyword: game_ref.update({"background_keyword": bg_keyword})
            if img_prompt:
                with st.spinner("MG maluje scenę..."):
                    scene_url = generate_image(img_prompt)
                    if scene_url: game_ref.update({"scene_image_url": scene_url})
            if map_prompt:
                with st.spinner("MG rysuje mapę świata..."):
                    map_url = generate_image(map_prompt, size="1792x1024")
                    if map_url: game_ref.update({"map_image_url": map_url})
        finally:
            game_ref.update({"is_typing": None})

def leave_game():
    if st.session_state.game_id and st.session_state.player_name:
        player_ref = db.collection("games").document(st.session_state.game_id).collection("players").document(st.session_state.player_name)
        player_ref.delete()
    st.session_state.game_id = None
    st.rerun()

# --- 7. Interfejs Użytkownika (GUI) ---

if not st.session_state.player_name:
    set_background_video("default")
    st.title("✨ Witaj w Świecie Przygód D&D ✨")
    st.header("Przedstaw się, aby rozpocząć")
    player_name_input = st.text_input("Wpisz swoje imię (będzie to Twój unikalny login)", key="player_login")
    if st.button("Zaloguj się", use_container_width=True, type="primary"):
        if player_name_input:
            st.session_state.player_name = player_name_input
            if db.collection("players").document(player_name_input).get().exists:
                st.session_state.character_exists = True
            st.rerun()
    st.stop()

if st.session_state.player_name and not st.session_state.character_exists:
    set_background_video("default")
    st.title(f"Witaj, {st.session_state.player_name}!")
    st.header("Stwórz swoją pierwszą postać")
    col1, col2 = st.columns([2, 1])
    with col1:
        st.info("Opisz w kilku słowach, kim chcesz być. AI zajmie się resztą!")
        character_concept = st.text_area("Np. 'Mroczny elf skrytobójca z dwoma sztyletami'", height=150)
        if st.button("Generuj Postać wg opisu", type="primary", use_container_width=True):
            if character_concept: generate_character(character_concept)
    with col2:
        st.info("...albo zdaj się na los!")
        if st.button("Losuj Postać!", use_container_width=True):
            concepts = ["a brave dwarven warrior", "a wise old human wizard", "a sneaky halfling rogue", "a noble elf paladin", "a chaotic gnome artificer"]
            generate_character(random.choice(concepts))
    st.stop()

if st.session_state.player_name and st.session_state.character_exists and not st.session_state.game_id:
    set_background_video("default")
    st.title(f"Witaj z powrotem, {st.session_state.player_name}!")
    st.header("Wybierz swoją przygodę")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Stwórz Nową Grę")
        if st.button("Stwórz Grę", use_container_width=True, type="primary"): create_game()
    with col2:
        st.subheader("Dołącz do Gry")
        join_id = st.text_input("Wpisz ID Gry", max_chars=6)
        if st.button("Dołącz do Gry", use_container_width=True):
            if join_id: join_game(join_id.upper())
    st.stop()

# --- Główny Ekran Gry ---
game_doc_ref = db.collection("games").document(st.session_state.game_id)
game_data = game_doc_ref.get().to_dict()
is_typing_by = game_data.get("is_typing")
set_background_video(game_data.get("background_keyword", "default"))

st.sidebar.title("Panel Gry")
st.sidebar.markdown(f"**ID Gry:** `{st.session_state.game_id}`")
st.sidebar.markdown(f"**Jesteś zalogowany jako:** `{st.session_state.player_name}`")
if st.sidebar.button("Wyjdź z gry", use_container_width=True):
    leave_game()
st.sidebar.markdown("---")

st.sidebar.subheader("Drużyna")
game_players_ref = game_doc_ref.collection("players").stream()
for game_player_doc in game_players_ref:
    player_name = game_player_doc.id
    game_player_data = game_player_doc.to_dict()
    player_global_data = db.collection("players").document(player_name).get().to_dict() or {}
    with st.sidebar.expander(f"**{player_name}** - {player_global_data.get('imię', '')}"):
        st.image(player_global_data.get('portrait_url', ''), use_container_width=True)
        st.write(f"**Klasa:** {player_global_data.get('klasa', '?')}")
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
    play_dice_sound()
    result = random.randint(1, int(dice_type[1:]))
    dice_roll_content = f"Rzucam kością {dice_type} i wyrzucam **{result}**."
    send_message(dice_roll_content, is_action=False)
    st.rerun()

# Nowy interfejs z zakładkami
tab1, tab2, tab3 = st.tabs(["📜 Kronika", "🎨 Scena", "🗺️ Mapa"])

with tab1:
    messages_query = game_doc_ref.collection("messages").order_by("timestamp", direction=firestore.Query.ASCENDING)
    chat_container = st.container()
    with chat_container:
        for doc in messages_query.stream():
            msg = doc.to_dict()
            if 'role' in msg and msg['role'] in ['user', 'assistant']:
                with st.chat_message(msg['role']):
                    st.write(f"**{msg.get('player_name', 'Nieznany gracz')}**")
                    st.markdown(msg.get('content', ''))
with tab2:
    st.image(game_data.get("scene_image_url", ""), use_column_width=True)
    st.caption("Obraz wygenerowany przez AI na podstawie opisu Mistrza Gry.")

with tab3:
    st.image(game_data.get("map_image_url", ""), use_column_width=True)
    st.caption("Mapa świata wygenerowana na początku przygody.")

placeholder_text = f"{is_typing_by} wykonuje ruch..." if is_typing_by else "Co robisz dalej?"
if prompt := st.chat_input(placeholder_text, disabled=(is_typing_by is not None)):
    send_message(prompt)
    st.rerun()

time.sleep(10)
st.rerun()
