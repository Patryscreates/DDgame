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
    page_title="D&D Multiplayer: Edycja Herosów",
    page_icon="👑",
    layout="wide"
)

# --- Słownik teł wideo i muzyki ---
AMBIANCE = {
    "karczma": {"video": "https://cdn.pixabay.com/video/2023/10/05/184813-873138840_large.mp4", "music": "https://cdn.pixabay.com/download/audio/2022/08/03/audio_a705a48937.mp3"},
    "las": {"video": "https://cdn.pixabay.com/video/2024/02/09/199582-911442728_large.mp4", "music": "https://cdn.pixabay.com/download/audio/2022/05/17/audio_eb21b38a79.mp3"},
    "jaskinia": {"video": "https://cdn.pixabay.com/video/2022/09/13/130113-750107243_large.mp4", "music": "https://cdn.pixabay.com/download/audio/2022/03/15/audio_510b653225.mp3"},
    "walka": {"video": "https://cdn.pixabay.com/video/2022/09/05/128189-747652290_large.mp4", "music": "https://cdn.pixabay.com/download/audio/2022/08/27/audio_39289965d1.mp3"},
    "default": {"video": "https://cdn.pixabay.com/video/2023/06/20/170942-838735238_large.mp4", "music": "https://cdn.pixabay.com/download/audio/2022/10/18/audio_7303a72b8b.mp3"}
}

# --- Mechaniki Gry: Poziomy i Umiejętności ---
XP_THRESHOLDS = {1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500}
CLASS_SKILLS = {
    "Łotrzyk": {3: ["Atak z zaskoczenia"]}, "Mag": {3: ["Kula ognia"]},
    "Wojownik": {3: ["Drugi oddech"]}, "Kleryk": {3: ["Leczenie ran"]}
}

def set_ambiance(keyword):
    """Ustawia dynamiczne tło i muzykę."""
    ambiance_data = AMBIANCE.get(keyword, AMBIANCE["default"])
    video_url, music_url = ambiance_data["video"], ambiance_data["music"]
    audio_key = f"audio_{keyword}"
    ambiance_html = f"""
    <style>
    .stApp {{ background: #000; }}
    #bg-video {{ position: fixed; right: 0; bottom: 0; min-width: 100%; min-height: 100%; z-index: -1; filter: brightness(0.5) blur(2px); }}
    [data-testid="stSidebar"], .main .block-container {{ background-color: rgba(14, 17, 23, 0.75); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px; padding: 1rem; }}
    [data-testid="stChatMessage"] {{ background-color: rgba(30, 35, 45, 0.9); border-radius: 10px; }}
    </style>
    <video autoplay loop muted playsinline id="bg-video"><source src="{video_url}" type="video/mp4"></video>
    <audio id="{audio_key}" src="{music_url}" autoplay loop></audio>
    """
    components.html(ambiance_html, height=0)

def play_dice_sound():
    components.html("""<script src="https://cdnjs.cloudflare.com/ajax/libs/tone/14.8.49/Tone.js"></script><script>const s=new Tone.Synth().toDestination();s.triggerAttackRelease("C5","16n",Tone.now()),s.triggerAttackRelease("G4","16n",Tone.now()+0.1);</script>""", height=0)

@st.cache_resource
def get_db_connection():
    try:
        creds_json = dict(st.secrets["firebase_credentials"])
        creds = service_account.Credentials.from_service_account_info(creds_json)
        return firestore.Client(credentials=creds, project=creds_json['project_id'])
    except Exception as e:
        st.error(f"Błąd połączenia z Firebase: {e}"); st.stop()

db = get_db_connection()

try:
    openai.api_key = st.secrets["OPENAI_API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("Brak klucza API OpenAI."); st.stop()

for key in ["player_name", "selected_character_name", "game_id"]:
    if key not in st.session_state: st.session_state[key] = None

def parse_response_from_dm(text):
    narrative = text
    img_prompt, bg_keyword, map_prompt, quest_update = None, None, None, None
    loot_items, xp_awards, choices, npcs, removed_npcs, combat_updates = [], [], [], [], [], []
    
    # Używamy re.findall do znalezienia wszystkich tagów i re.sub do ich usunięcia na końcu
    tags_to_remove = []
    
    img_match = re.search(r'\[IMG: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if img_match: img_prompt = img_match.group(1).strip(); tags_to_remove.append(img_match.group(0))

    bg_match = re.search(r'\[TLO: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if bg_match: bg_keyword = bg_match.group(1).strip().lower(); tags_to_remove.append(bg_match.group(0))

    map_match = re.search(r'\[MAPA: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if map_match: map_prompt = map_match.group(1).strip(); tags_to_remove.append(map_match.group(0))

    quest_match = re.search(r'\[ZADANIE: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if quest_match: quest_update = quest_match.group(1).strip(); tags_to_remove.append(quest_match.group(0))
    
    choice_match = re.search(r'\[WYBÓR: (.*?)\]', text, re.DOTALL | re.IGNORECASE)
    if choice_match: choices = [c.strip().strip('"') for c in choice_match.group(1).split(';')]; tags_to_remove.append(choice_match.group(0))

    for tag_type, pattern, storage in [("LOOT", r'\[LOOT: (.*?);(.*?);(.*?)\]', loot_items), ("XP", r'\[XP: (.*?);(\d+)\]', xp_awards), ("NPC", r'\[NPC: (.*?);(.*?);(.*?)\]', npcs), ("NPC_REMOVE", r'\[NPC_REMOVE: (.*?)\]', removed_npcs), ("WALKA", r'\[WALKA: (.*?)\]', combat_updates)]:
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        for match in matches:
            tags_to_remove.append(re.search(re.escape(match if isinstance(match, str) else match[0]), text).group(0))
            if tag_type == "LOOT": storage.append({"player": match[0].strip(), "item": match[1].strip(), "desc": match[2].strip()})
            elif tag_type == "XP": storage.append({"player": match[0].strip(), "amount": int(match[1])})
            elif tag_type == "NPC": storage.append({"name": match[0].strip(), "desc": match[1].strip(), "portrait_prompt": match[2].strip()})
            elif tag_type == "NPC_REMOVE": storage.append(match.strip())
            elif tag_type == "WALKA": storage.append(match.strip())

    for tag in set(tags_to_remove): narrative = narrative.replace(tag, '')
    
    return narrative.strip(), img_prompt, bg_keyword, map_prompt, quest_update, loot_items, xp_awards, choices, npcs, removed_npcs, combat_updates

@st.cache_data(ttl=3600)
def generate_image(prompt, size="1024x1024"):
    try:
        response = openai.images.generate(model="dall-e-3", prompt=f"digital painting, {prompt}", n=1, size=size, quality="standard")
        return response.data[0].url
    except Exception as e:
        return None

def create_game():
    game_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    game_ref = db.collection("games").document(game_id)
    game_ref.set({
        "created_at": firestore.SERVER_TIMESTAMP, "active": True, "is_typing": None, "in_combat": False, "current_turn_index": 0,
        "scene_image_url": "https://placehold.co/1024x1024/0E1117/FFFFFF?text=Przygoda+si%C4%99+zaczyna...&font=raleway",
        "map_image_url": "https://placehold.co/1024x1024/0E1117/FFFFFF?text=Mapa+niezbadanych+krain...&font=raleway",
        "background_keyword": "default", "quest_log": "Twoja przygoda jeszcze się nie rozpoczęła.", "choices": []
    })
    join_game(game_id)

def join_game(game_id):
    game_ref = db.collection("games").document(game_id)
    if not game_ref.get().exists:
        st.error("Gra o podanym ID nie istnieje."); return
    
    char_ref = db.collection("players").document(st.session_state.player_name).collection("characters").document(st.session_state.selected_character_name).get()
    if char_ref.exists:
        char_data = char_ref.to_dict()
        game_player_ref = game_ref.collection("players").document(st.session_state.selected_character_name)
        game_player_ref.set({"current_hp": char_data.get("punkty_życia", "100"), "player_account": st.session_state.player_name, "joined_at": firestore.SERVER_TIMESTAMP})
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
                    char_data['portrait_url'] = portrait_url
                char_data['xp'] = 0; char_data['level'] = 1
                db.collection("players").document(st.session_state.player_name).collection("characters").document(char_data['imię']).set(char_data)
                st.session_state.selected_character_name = char_data['imię']
                st.rerun()
            else:
                st.error("Nie udało się wygenerować postaci. Spróbuj ponownie.")
        except Exception as e:
            st.error(f"Wystąpił błąd: {e}")

def send_message(content, is_action=True):
    game_ref = db.collection("games").document(st.session_state.game_id)
    messages_ref = game_ref.collection("messages")
    game_ref.update({"is_typing": st.session_state.selected_character_name, "choices": []})
    messages_ref.add({"role": "user", "content": content, "timestamp": firestore.SERVER_TIMESTAMP, "player_name": st.session_state.selected_character_name})
    if not is_action:
        game_ref.update({"is_typing": None}); return
    with st.spinner("Mistrz Gry myśli..."):
        game_ref.update({"is_typing": "Mistrz Gry"})
        history_query = messages_ref.order_by("timestamp", direction=firestore.Query.ASCENDING).limit(20)
        system_prompt = "Jesteś Mistrzem Gry D&D. Prowadź narrację. Używaj tagów: `[IMG: opis sceny]`, `[TLO: lokacja]`, `[ZADANIE: cel misji]`, `[WYBÓR: \"Opcja 1\"; \"Opcja 2\"]`, `[XP: imie_postaci;ilość]`, `[LOOT: imie_postaci;nazwa;opis]`, `[NPC: imię;opis;prompt portretu]`, `[NPC_REMOVE: imię]`. Aby rozpocząć walkę, użyj `[WALKA: START;potwór1;potwór2;...]`. Aby zakończyć `[WALKA: KONIEC]`. W walce, po akcji gracza, opisz co się stało i wykonaj ruch potwora."
        messages_for_ai = [{"role": "system", "content": system_prompt}]
        for doc in history_query.stream():
            msg = doc.to_dict()
            ai_content = f"{msg.get('player_name', '')}: {msg.get('content', '')}" if msg.get('role') == 'user' else msg.get('content', '')
            messages_for_ai.append({"role": msg.get('role', 'user'), "content": ai_content})
        try:
            response = openai.chat.completions.create(model="gpt-4-turbo", messages=messages_for_ai, temperature=0.9)
            dm_response_raw = response.choices[0].message.content
            narrative, img_prompt, bg_keyword, map_prompt, quest_update, loot_items, xp_awards, choices, npcs, removed_npcs, combat_updates = parse_response_from_dm(dm_response_raw)
            messages_ref.add({"role": "assistant", "content": narrative, "timestamp": firestore.SERVER_TIMESTAMP, "player_name": "Mistrz Gry"})
            if bg_keyword: game_ref.update({"background_keyword": bg_keyword})
            if quest_update: game_ref.update({"quest_log": quest_update})
            if choices: game_ref.update({"choices": choices})
            
            for loot in loot_items:
                # Znajdź konto gracza na podstawie imienia postaci
                game_players = game_ref.collection("players").stream()
                for p in game_players:
                    if p.id == loot["player"]:
                        player_account = p.to_dict().get("player_account")
                        db.collection("players").document(player_account).collection("characters").document(loot["player"]).collection("inventory").add({"item_name": loot["item"], "description": loot["desc"]})
                        messages_ref.add({"role": "system", "content": f"*{loot['player']} otrzymuje: {loot['item']}!*", "timestamp": firestore.SERVER_TIMESTAMP, "player_name": "System"})
                        break
            
            for xp in xp_awards:
                game_players = game_ref.collection("players").stream()
                for p in game_players:
                    if p.id == xp["player"]:
                        player_account = p.to_dict().get("player_account")
                        char_ref = db.collection("players").document(player_account).collection("characters").document(xp["player"])
                        char_ref.update({"xp": firestore.Increment(xp["amount"])})
                        messages_ref.add({"role": "system", "content": f"*{xp['player']} otrzymuje {xp['amount']} PD!*", "timestamp": firestore.SERVER_TIMESTAMP, "player_name": "System"})
                        char_doc = char_ref.get().to_dict()
                        current_level, current_xp = char_doc.get("level", 1), char_doc.get("xp", 0)
                        next_level = current_level + 1
                        if next_level in XP_THRESHOLDS and current_xp >= XP_THRESHOLDS[next_level]:
                            char_ref.update({"level": next_level})
                            messages_ref.add({"role": "system", "content": f"🎉 **{xp['player']} awansuje na poziom {next_level}!** 🎉", "timestamp": firestore.SERVER_TIMESTAMP, "player_name": "System"})
                        break
            
            for npc_data in npcs:
                with st.spinner(f"AI tworzy postać {npc_data['name']}..."):
                    portrait_url = generate_image(npc_data['portrait_prompt'])
                    npc_data['portrait_url'] = portrait_url
                    game_ref.collection("npcs").document(npc_data['name']).set(npc_data)
            
            for npc_name in removed_npcs: game_ref.collection("npcs").document(npc_name).delete()
            
            for combat_update in combat_updates:
                parts = combat_update.split(';')
                command = parts[0].upper()
                if command == "START":
                    game_ref.update({"in_combat": True, "background_keyword": "walka", "current_turn_index": 0})
                    for doc in game_ref.collection("combatants").stream(): doc.reference.delete()
                    for player_doc in game_ref.collection("players").stream():
                        game_ref.collection("combatants").add({"name": player_doc.id, "type": "player", "initiative": random.randint(1,20)})
                    for monster_name in parts[1:]:
                        game_ref.collection("combatants").add({"name": monster_name.strip(), "type": "monster", "hp": 100, "initiative": random.randint(1,20)})
                elif command == "KONIEC":
                    game_ref.update({"in_combat": False})

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
    if st.session_state.game_id and st.session_state.selected_character_name:
        player_ref = db.collection("games").document(st.session_state.game_id).collection("players").document(st.session_state.selected_character_name)
        if player_ref.get().exists: player_ref.delete()
    st.session_state.game_id = None
    st.rerun()

# --- 7. GŁÓWNA FUNKCJA WYŚWIETLAJĄCA ---
def main_gui():
    if not st.session_state.player_name:
        set_ambiance("default")
        st.title("✨ Witaj w Świecie Przygód D&D ✨")
        st.header("Przedstaw się, aby rozpocząć")
        player_name_input = st.text_input("Wpisz swoje imię (będzie to Twój unikalny login)", key="player_login")
        if st.button("Zaloguj się", use_container_width=True, type="primary"):
            if player_name_input:
                st.session_state.player_name = player_name_input
                st.rerun()
        return

    char_collection_ref = db.collection("players").document(st.session_state.player_name).collection("characters")
    characters = [doc.id for doc in char_collection_ref.stream()]

    if not st.session_state.selected_character_name:
        set_ambiance("default")
        st.title(f"Witaj, {st.session_state.player_name}!")
        st.header("Wybierz lub stwórz postać")
        
        if not characters:
            st.info("Nie masz jeszcze żadnej postaci. Czas stworzyć swojego pierwszego bohatera!")
        else:
            st.subheader("Wybierz istniejącą postać:")
            selected_char = st.selectbox("Twoi bohaterowie", characters)
            if st.button("Graj jako " + selected_char, use_container_width=True, type="primary"):
                st.session_state.selected_character_name = selected_char
                st.rerun()
        
        st.markdown("---")
        st.subheader("...lub stwórz nową:")
        with st.expander("Kreator postaci"):
            character_concept = st.text_area("Opisz w kilku słowach, kim chcesz być", height=100)
            if st.button("Generuj Postać", use_container_width=True):
                if character_concept: generate_character(character_concept)
        return

    if not st.session_state.game_id:
        set_ambiance("default")
        st.title(f"Grasz jako {st.session_state.selected_character_name}")
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
        if st.button("Zmień postać"):
            st.session_state.selected_character_name = None
            st.rerun()
        return

    game_doc_ref = db.collection("games").document(st.session_state.game_id)
    game_data = game_doc_ref.get().to_dict()
    if not game_data:
        st.warning("Wczytywanie danych gry..."); time.sleep(2); st.rerun(); return

    is_typing_by = game_data.get("is_typing")
    set_ambiance(game_data.get("background_keyword", "default"))

    st.sidebar.title("Panel Gry")
    st.sidebar.markdown(f"**ID Gry:** `{st.session_state.game_id}`")
    st.sidebar.markdown(f"**Gracz:** `{st.session_state.player_name}`")
    if st.sidebar.button("Wyjdź z gry", use_container_width=True):
        leave_game()
    st.sidebar.markdown("---")
    
    st.sidebar.subheader("📜 Dziennik Zadań")
    st.sidebar.info(game_data.get("quest_log", "Brak aktywnego zadania."))
    st.sidebar.markdown("---")

    with st.sidebar.expander("🗺️ Pokaż Mapę Świata"):
        st.image(game_data.get("map_image_url", ""), use_container_width=True)
    st.sidebar.markdown("---")
    
    st.sidebar.subheader("👥 Postacie w pobliżu")
    npcs_ref = game_doc_ref.collection("npcs").stream()
    npcs_list = list(npcs_ref)
    if not npcs_list: st.sidebar.caption("Nikogo tu nie ma...")
    else:
        for npc_doc in npcs_list:
            npc_data = npc_doc.to_dict()
            with st.sidebar.container():
                st.image(npc_data.get('portrait_url', "https://placehold.co/512x512/333/FFF?text=Brak+Portretu"), use_container_width=True)
                st.write(f"**{npc_data.get('name')}**")
                st.caption(npc_data.get('desc'))
                if st.button(f"Porozmawiaj z {npc_data.get('name')}", key=f"talk_{npc_doc.id}", use_container_width=True):
                    send_message(f"[Rozmawia z {npc_data.get('name')}]")
                    st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Drużyna")
    game_players_ref = game_doc_ref.collection("players").stream()
    for game_player_doc in game_players_ref:
        char_name = game_player_doc.id
        game_player_data = game_player_doc.to_dict()
        player_account = game_player_data.get("player_account")
        player_global_data = db.collection("players").document(player_account).collection("characters").document(char_name).get().to_dict() or {}
        with st.sidebar.expander(f"**{char_name}** ({player_account})", expanded=char_name == st.session_state.selected_character_name):
            portrait_url = player_global_data.get('portrait_url')
            if isinstance(portrait_url, str) and portrait_url.startswith('http'):
                st.image(portrait_url, use_container_width=True)
            else:
                st.image("https://placehold.co/512x512/333/FFF?text=Brak+Portretu", use_container_width=True)
            
            level = player_global_data.get('level', 1)
            xp = player_global_data.get('xp', 0)
            xp_for_next_level = XP_THRESHOLDS.get(level + 1, xp)
            st.progress(xp / xp_for_next_level if xp_for_next_level > 0 else 1.0, text=f"Poziom: {level} ({xp}/{xp_for_next_level} XP)")
            
            hp_key = f"hp_{char_name}_{st.session_state.game_id}"
            current_hp = int(game_player_data.get('current_hp', 0))
            new_hp = st.number_input("Punkty Życia", value=current_hp, key=hp_key, step=1)
            if new_hp != current_hp:
                update_player_hp(st.session_state.game_id, char_name, new_hp)
                st.toast(f"Zaktualizowano HP dla {char_name}!")
            
            st.write("**Umiejętności:**")
            player_class = player_global_data.get('klasa')
            player_skills = []
            for lvl_unlocked, skills in CLASS_SKILLS.get(player_class, {}).items():
                if level >= lvl_unlocked:
                    player_skills.extend(skills)
            if not player_skills: st.caption("Brak")
            else:
                for skill in player_skills:
                    if st.button(skill, key=f"skill_{skill}_{char_name}", use_container_width=True):
                        send_message(f"[Używa umiejętności: {skill}]"); st.rerun()
            
            st.write("**Ekwipunek:**")
            inventory_ref = db.collection("players").document(player_account).collection("characters").document(char_name).collection("inventory").stream()
            inventory_items = list(inventory_ref)
            if not inventory_items: st.caption("Pusto")
            else:
                for item_doc in inventory_items:
                    item_data = item_doc.to_dict()
                    item_name = item_data.get('item_name', 'Nieznany przedmiot')
                    item_cols = st.columns([3, 1, 1]); item_cols[0].markdown(f"**{item_name}**"); 
                    if item_cols[1].button("Użyj", key=f"use_{item_doc.id}", use_container_width=True):
                        send_message(f"[Używa: {item_name}]", is_action=True); st.rerun()
                    if item_cols[2].button("Wyrzuć", key=f"drop_{item_doc.id}", use_container_width=True):
                        db.collection("players").document(player_account).collection("characters").document(char_name).collection("inventory").document(item_doc.id).delete()
                        send_message(f"[Wyrzuca: {item_name}]", is_action=False); st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("🎲 Rzut Kością")
    dice_type = st.sidebar.selectbox("Typ kości", ["k20", "k12", "k10", "k8", "k6", "k4"])
    if st.sidebar.button(f"Rzuć {dice_type}!"):
        play_dice_sound()
        result = random.randint(1, int(dice_type[1:]))
        dice_roll_content = f"Rzucam kością {dice_type} i wyrzucam **{result}**."
        send_message(dice_roll_content, is_action=False)
        st.rerun()

    col1, col2 = st.columns([2, 1.2])
    with col1:
        st.header("📜 Kronika Przygody")
        messages_query = game_doc_ref.collection("messages").order_by("timestamp", direction=firestore.Query.ASCENDING)
        chat_container = st.container()
        with chat_container:
            for doc in messages_query.stream():
                msg = doc.to_dict()
                if 'role' in msg and msg['role'] in ['user', 'assistant', 'system']:
                    with st.chat_message(msg.get('role', 'user')):
                        st.write(f"**{msg.get('player_name', 'Nieznany gracz')}**")
                        st.markdown(msg.get('content', ''))
    with col2:
        st.header("🎨 Wizualizacja Sceny")
        st.image(game_data.get("scene_image_url", ""), use_container_width=True)
        st.caption("Obraz wygenerowany przez AI na podstawie opisu Mistrza Gry.")

    choices = game_data.get("choices", [])
    is_my_turn = (is_typing_by is None and not choices) or (is_typing_by is None and choices and st.session_state.selected_character_name in [p.id for p in game_doc_ref.collection("players").stream()])

    if choices:
        st.write("---")
        st.subheader("Co robisz?")
        choice_cols = st.columns(len(choices))
        for i, choice in enumerate(choices):
            if choice_cols[i].button(choice, use_container_width=True, disabled=(not is_my_turn)):
                send_message(choice)
                st.rerun()

    placeholder_text = f"{is_typing_by} wykonuje ruch..." if is_typing_by else "Co robisz dalej?"
    if prompt := st.chat_input(placeholder_text, disabled=(not is_my_turn or bool(choices))):
        send_message(prompt)
        st.rerun()

    time.sleep(10)
    st.rerun()

if __name__ == "__main__":
    main_gui()
