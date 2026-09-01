import stable_whisper

# ==========================================
# 1. CONFIGURATION
# ==========================================
AUDIO_PATH = "vocal_isole.wav"
TEXT_PATH = "paroles.txt"
LANGUE = "he"
ANTICIPATION_SEC = 2.0  # Temps de préparation avant que la phrase soit chantée

print("⚙️ Chargement du modèle Whisper (base)...")
model = stable_whisper.load_model("base")

# ==========================================
# 2. CHARGEMENT DU TEXTE
# ==========================================
print("📄 Chargement des paroles...")
with open(TEXT_PATH, "r", encoding="utf-8") as f:
    lignes_originales = [line.strip() for line in f.readlines() if line.strip()]

texte_complet = " ".join(lignes_originales)

# ==========================================
# 3. ALIGNEMENT
# ==========================================
print("⏳ Alignement forcé en cours (cela peut prendre quelques minutes)...")
# stable_whisper est parfait pour forcer l'alignement d'un texte externe
result = model.align(AUDIO_PATH, texte_complet, language=LANGUE)

# ==========================================
# 4. GÉNÉRATION DU FICHIER .ASS (KARAOKÉ HÉBREU)
# ==========================================
print("🎨 Génération du fichier karaoké (.ass) optimisé pour l'Hébreu (RTL)...")

def format_ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

# En-tête ASS avec police Arial Hebrew pour éviter les bugs d'affichage sur Mac
ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial Hebrew,65,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,10,10,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# On récupère tous les mots alignés dans une liste plate
tous_les_mots = []
for segment in result.segments:
    for word_info in segment.words:
        tous_les_mots.append(word_info)

index_mot_actuel = 0
temps_fin_precedent = 0.0

with open("karaoke.ass", "w", encoding="utf-8") as f_ass:
    f_ass.write(ass_header)

    for ligne in lignes_originales:
        mots_de_la_ligne = ligne.split()
        nombre_de_mots = len(mots_de_la_ligne)

        if index_mot_actuel >= len(tous_les_mots):
            break

        index_fin_cible = index_mot_actuel + nombre_de_mots - 1
        if index_fin_cible >= len(tous_les_mots):
            index_fin_cible = len(tous_les_mots) - 1

        # On isole les mots de CETTE phrase
        mots_alignes = tous_les_mots[index_mot_actuel : index_fin_cible + 1]

        if not mots_alignes:
            break

        mot_debut = mots_alignes[0]
        mot_fin = mots_alignes[-1]

        # Logique d'anticipation pour voir la phrase avant
        nouveau_debut = max(0.0, mot_debut.start - ANTICIPATION_SEC)
        nouveau_debut = max(nouveau_debut, temps_fin_precedent)

        start_ass = format_ass_time(nouveau_debut)
        end_ass = format_ass_time(mot_fin.end)

        # Attente initiale avant de chanter
        delai_initial_sec = mot_debut.start - nouveau_debut
        delai_initial_cs = int(round(delai_initial_sec * 100))

        # \u202B est un caractère invisible qui force l'affichage de droite à gauche
        texte_karaoke = f"\u202B{{\\k{delai_initial_cs}}}"

        last_end = mot_debut.start

        for i, w in enumerate(mots_alignes):
            # L'avantage ici : on utilise le VRAI mot de ton fichier texte
            vrai_mot_texte = mots_de_la_ligne[i] if i < len(mots_de_la_ligne) else w.word

            gap_sec = w.start - last_end
            if gap_sec > 0.05:
                gap_cs = int(round(gap_sec * 100))
                texte_karaoke += f"{{\\k{gap_cs}}} "
            elif i > 0:
                texte_karaoke += " "

            # Remplissage du mot en entier (\k) pour l'hébreu
            duree_mot_sec = w.end - w.start
            duree_mot_cs = int(round(duree_mot_sec * 100))
            texte_karaoke += f"{{\\k{duree_mot_cs}}}{vrai_mot_texte}"

            last_end = w.end

        # \u202C ferme le bloc de droite à gauche
        texte_karaoke += "\u202C"

        # Écriture de la ligne ASS
        f_ass.write(f"Dialogue: 0,{start_ass},{end_ass},Karaoke,,0,0,0,,{texte_karaoke}\n")

        # Sauvegarde pour ne pas écraser la phrase suivante
        temps_fin_precedent = mot_fin.end
        index_mot_actuel += nombre_de_mots

print("🎉 Fichier 'karaoke.ass' généré et prêt pour FFmpeg !")
