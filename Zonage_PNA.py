import streamlit as st
import pandas as pd
import json
from shapely.geometry import Point, shape
import time
import re
import geopandas as gpd
import requests

# Configuration de la page
st.set_page_config(page_title="Vérificateur de Zones PNA", page_icon="🦇", layout="wide")

# Titre et description
st.title("Vérificateur de Zones PNA (Plans Nationaux d'Actions)")
st.markdown("Cet outil vous permet de vérifier si une adresse ou des coordonnées se trouvent dans une zone de Plan National d'Actions.")

# Fonction de géocodage utilisant l'API adresse.data.gouv.fr
def get_coordinates(address):
    with st.spinner("Recherche des coordonnées..."):
        # Utiliser l'API adresse.data.gouv.fr (spécifique à la France)
        try:
            # Encoder l'adresse pour l'URL
            encoded_address = requests.utils.quote(address)
            
            # URL de l'API française
            url = f"https://api-adresse.data.gouv.fr/search/?q={encoded_address}&limit=1"
            
            # Faire la requête
            response = requests.get(url, timeout=10)
            
            # Traiter la réponse
            if response.status_code == 200:
                data = response.json()
                
                # Vérifier si des résultats ont été trouvés
                if data and data.get('features') and len(data['features']) > 0:
                    # Récupérer les coordonnées (attention: l'API renvoie [lon, lat])
                    lon, lat = data['features'][0]['geometry']['coordinates']
                    
                    # Récupérer également l'adresse complète pour l'afficher
                    full_address = data['features'][0]['properties'].get('label', address)
                    score = data['features'][0]['properties'].get('score', 0) * 100
                    
                    # Afficher un message de succès
                    st.success(f"✅ Adresse trouvée: {full_address} (confiance: {score:.1f}%)")
                    
                    # Convertir en Lambert 93 pour faciliter les comparaisons avec nos données
                    point_wgs84 = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
                    point_l93 = point_wgs84.to_crs("EPSG:2154")
                    x_l93, y_l93 = point_l93.geometry[0].x, point_l93.geometry[0].y
                    
                    # Retourner les coordonnées (WGS84 pour l'affichage, Lambert93 pour l'analyse)
                    return (lat, lon, x_l93, y_l93, full_address)
                else:
                    st.warning("❌ Aucun résultat trouvé pour cette adresse")
            else:
                st.warning(f"⚠️ Erreur lors de la requête: {response.status_code}")
                
        except Exception as e:
            st.error(f"⚠️ Erreur lors de la requête à l'API adresse.data.gouv.fr: {str(e)}")
            
        # Si l'API française échoue, proposer la saisie manuelle
        st.error("⚠️ Impossible de géocoder cette adresse.")
        
        # Option pour saisie manuelle
        if st.checkbox("✏️ Saisir manuellement les coordonnées ?"):
            col1, col2 = st.columns(2)
            with col1:
                manual_lat = st.number_input("Latitude WGS84", value=43.6, format="%.6f")
            with col2:
                manual_lon = st.number_input("Longitude WGS84", value=2.7, format="%.6f")
            
            if st.button("Utiliser ces coordonnées"):
                # Convertir en Lambert 93
                point_wgs84 = gpd.GeoDataFrame(geometry=[Point(manual_lon, manual_lat)], crs="EPSG:4326")
                point_l93 = point_wgs84.to_crs("EPSG:2154")
                x_l93, y_l93 = point_l93.geometry[0].x, point_l93.geometry[0].y
                
                return (manual_lat, manual_lon, x_l93, y_l93, f"Coordonnées manuelles: {manual_lat}, {manual_lon}")
                
    return None

# Fonction pour vérifier si un point est dans une zone PNA
def is_in_pna(lat, lon, x_l93, y_l93, all_data_sources):
    try:
        # Point en WGS84 pour l'affichage
        point_wgs84 = Point(lon, lat)
        # Point en Lambert 93 pour l'analyse
        point_l93 = Point(x_l93, y_l93)
        
        results = []
        
        # Vérifier dans chaque fichier chargé
        for file_name, file_info in all_data_sources.items():
            data_source = file_info["data"]
            data_type = file_info["type"]
            
            # Convertir le point en Lambert 93 pour comparaison
            point_buffer = point_l93.buffer(1)  # 1 mètre en Lambert 93
            
            # Parcourir les features du GeoJSON
            for feature in data_source['features']:
                try:
                    pna_shape = shape(feature['geometry'])
                    properties = feature['properties']
                    
                    # Vérification directe
                    if pna_shape.contains(point_l93) or pna_shape.intersects(point_buffer):
                        # Traitement spécifique pour les chiroptères
                        if data_type == "Chiroptères":
                            enjeu = properties.get("t_enjeux", "Indéterminé")
                            properties["enjeu_détaillé"] = enjeu
                        
                        # Ajouter le type de PNA et le nom du fichier
                        properties["type_pna"] = data_type
                        properties["fichier_source"] = file_name
                        
                        results.append(properties)
                except Exception as e:
                    continue
        
        if results:
            return True, results
        else:
            return False, None
    except Exception as e:
        st.error(f"Erreur lors de la vérification des zones: {str(e)}")
        return False, None
# Structure à deux colonnes
col1, col2 = st.columns([1, 3])

# Colonne de gauche pour le chargement du fichier
with col1:
    st.header("Chargement des données")
    
    # Options de données à vérifier
    st.subheader("Type de données")
    pna_type = st.selectbox(
        "Sélectionner un type de PNA",
        [
            "Détection automatique", 
            "Chiroptères", 
            "Odonates", 
            "Pie-grièche grise", 
            "Pie-grièche méridionale", 
            "Pie-grièche à tête rousse"
        ]
    )
    
    # Variable pour stocker les données de tous les fichiers chargés
    all_data_sources = {}
    file_types = {}
    
    uploaded_files = st.file_uploader(
        "Fichiers des zones PNA", 
        type=["geojson", "json"],
        accept_multiple_files=True,
        help="Formats supportés: GeoJSON, JSON en projection Lambert 93 (EPSG:2154)"
    )
    
    if uploaded_files:
        for uploaded_file in uploaded_files:
            try:
                file_extension = uploaded_file.name.split('.')[-1].lower()
                file_name = uploaded_file.name
                
                # Variable pour stocker le type détecté
                detected_type = "Type non détecté"
                
                # Détecter automatiquement le type de PNA si demandé
                if pna_type == "Détection automatique":
                    # Première tentative : détection par le nom du fichier
                    if "chiroptere" in file_name.lower() or "chauve" in file_name.lower():
                        detected_type = "Chiroptères"
                    elif "odonat" in file_name.lower() or "libellule" in file_name.lower():
                        detected_type = "Odonates"
                    elif "griseche" in file_name.lower() and "grise" in file_name.lower():
                        detected_type = "Pie-grièche grise"
                    elif "griseche" in file_name.lower() and "merid" in file_name.lower():
                        detected_type = "Pie-grièche méridionale"
                    elif "griseche" in file_name.lower() and "rousse" in file_name.lower() or "tete rousse" in file_name.lower():
                        detected_type = "Pie-grièche à tête rousse"
                    
                    current_type = detected_type
                else:
                    current_type = pna_type
                
                if file_extension in ['geojson', 'json']:
                    data = json.load(uploaded_file)
                    
                    # Vérifier si c'est un GeoJSON valide
                    if 'type' in data and 'features' in data:
                        # Si on est en mode détection automatique et que le type n'a pas été détecté par le nom
                        if pna_type == "Détection automatique" and detected_type == "Type non détecté":
                            # Parcourir quelques features pour analyser le contenu
                            for feature in data['features'][:10]:  # Limiter à 10 features pour des raisons de performance
                                if 'properties' in feature:
                                    props = feature['properties']
                                    
                                    # Chercher des indices dans les propriétés
                                    if 'n_espece' in props:
                                        espece = str(props['n_espece']).lower()
                                        if 'chauve' in espece or 'chiroptère' in espece or 'chiroptere' in espece:
                                            detected_type = "Chiroptères"
                                            break
                                        elif 'odonates' in espece or 'libellule' in espece:
                                            detected_type = "Odonates"
                                            break
                                        elif 'grièche' in espece or 'grieche' in espece:
                                            if 'grise' in espece and not 'tête' in espece and not 'tete' in espece:
                                                detected_type = "Pie-grièche grise"
                                                break
                                            elif 'mérid' in espece or 'merid' in espece:
                                                detected_type = "Pie-grièche méridionale"
                                                break
                                            elif 'tête rousse' in espece or 'tete rousse' in espece:
                                                detected_type = "Pie-grièche à tête rousse"
                                                break
                            
                            # Mise à jour du type détecté
                            if detected_type != "Type non détecté":
                                current_type = detected_type
                        
                        # Stocker le fichier avec son type détecté
                        if current_type == "Type non détecté":
                            current_type = "Type inconnu"
                        
                        # Ajouter au dictionnaire de sources de données
                        all_data_sources[file_name] = {
                            "data": data,
                            "type": current_type
                        }
                        
                        file_types[file_name] = "geojson"
                        
                        st.success(f"Fichier '{file_name}' chargé avec succès. Type détecté: {current_type}. {len(data['features'])} zones.")
                    else:
                        st.error(f"Format du fichier '{file_name}' GeoJSON invalide")
            except Exception as e:
                st.error(f"Erreur lors du chargement de '{file_name}': {str(e)}")
        
        # Afficher un résumé des fichiers chargés
        if all_data_sources:
            st.subheader("Fichiers chargés")
            
            # Créer un DataFrame pour l'affichage
            file_summary = []
            for file_name, file_info in all_data_sources.items():
                feature_count = len(file_info["data"]["features"])
                file_summary.append({
                    "Fichier": file_name,
                    "Type PNA": file_info["type"],
                    "Nombre de zones": feature_count
                })
            
            # Afficher le tableau récapitulatif
            st.dataframe(pd.DataFrame(file_summary))
    
    # Ajouter des informations sur l'API utilisée
    st.markdown("---")
    if all_data_sources:
        st.success(f"✅ {len(all_data_sources)} fichier(s) GeoJSON chargé(s), prêt pour la vérification.")
    
    st.info("✨ Cette application utilise l'API adresse.data.gouv.fr pour le géocodage des adresses françaises.")

# Colonne de droite pour la vérification
with col2:
    st.header("Vérification")
    
    # Initialisation des variables de session si elles n'existent pas
    if 'reset_pressed' not in st.session_state:
        st.session_state.reset_pressed = False
    if 'last_address' not in st.session_state:
        st.session_state.last_address = ""
    if 'last_lat' not in st.session_state:
        st.session_state.last_lat = 43.6
    if 'last_lon' not in st.session_state:
        st.session_state.last_lon = 2.7
    
    # Fonction pour réinitialiser les champs
    def reset_fields():
        st.session_state.reset_pressed = True
        st.session_state.last_address = ""
        st.session_state.last_lat = 43.6
        st.session_state.last_lon = 2.7
    
    # Bouton de réinitialisation
    reset_col, spacer = st.columns([1, 3])
    with reset_col:
        st.button("🔄 Nouvelle recherche", on_click=reset_fields, help="Réinitialiser les champs et effacer les résultats")
    
    # Mode de saisie
    input_mode = st.radio("Mode", ["Adresse", "Coordonnées"])

    # Placeholder pour les résultats (vide au début)
    results_placeholder = st.empty()
    
    # Conteneur pour les résultats
    with results_placeholder.container():
        if input_mode == "Adresse":
            # Utiliser la dernière adresse ou une chaîne vide si réinitialisation
            if st.session_state.reset_pressed:
                initial_address = ""
                st.session_state.reset_pressed = False  # Réinitialiser le flag
            else:
                initial_address = st.session_state.last_address
                
            address = st.text_input("Entrez une adresse", value=initial_address,
                                   help="Exemple: 1 Place de la Mairie, 34000 Montpellier")
            # Stocker l'adresse actuelle
            st.session_state.last_address = address
            
            check_button = st.button("Vérifier l'adresse")
            
            # Ne continuer que si on a cliqué sur le bouton et qu'un fichier est chargé
            if check_button and address:
                if not all_data_sources:
                    st.error("Veuillez d'abord charger au moins un fichier GeoJSON")
                else:
                    # Effacer le contenu du placeholder
                    results_placeholder.empty()
                    
                    # Recréer un conteneur pour les nouveaux résultats
                    with results_placeholder.container():
                        st.write(f"Adresse saisie: {address}")
                        
                        # Géocodage
                        coordinates = get_coordinates(address)
                        if coordinates:
                            lat, lon, x_l93, y_l93, full_address = coordinates
                            st.write(f"Coordonnées WGS84: {lat:.6f}, {lon:.6f}")
                            st.write(f"Coordonnées Lambert 93: {x_l93:.2f}, {y_l93:.2f}")
                            
                            # Vérification PNA avec les coordonnées Lambert 93
                            in_pna, results = is_in_pna(lat, lon, x_l93, y_l93, all_data_sources)
                            
                            # Afficher le résultat textuel
                            if in_pna:
                                st.success(f"✅ Cette adresse est située dans {len(results)} zone(s) PNA")
                                
                                # Créer un tableau avec tous les résultats
                                result_data = []
                                for i, props in enumerate(results, 1):
                                    pna_type = props.get("type_pna", "Inconnu")
                                    file_source = props.get("fichier_source", "")
                                    espece = props.get("n_espece", "Non spécifié")
                                    enjeu = ""
                                    
                                    if pna_type == "Chiroptères":
                                        enjeu = props.get("enjeu_détaillé", "Indéterminé")
                                    
                                    result_data.append({
                                        "Zone": i,
                                        "Type PNA": pna_type, 
                                        "Espèce": espece,
                                        "Enjeu": enjeu,
                                        "Fichier source": file_source
                                    })
                                
                                # Afficher le tableau récapitulatif
                                st.subheader("Zones PNA détectées:")
                                st.dataframe(pd.DataFrame(result_data))
                                
                                # Affichage détaillé pour chaque zone
                                for i, props in enumerate(results, 1):
                                    with st.expander(f"Détails de la zone {i} - {props.get('n_espece', 'Non spécifié')}"):
                                        # Filtrer et trier les propriétés pour plus de clarté
                                        important_props = {}
                                        other_props = {}
                                        
                                        # Définir les propriétés importantes à afficher en premier
                                        priority_keys = ["n_espece", "t_enjeux", "enjeu_détaillé", "type_pna", "richessesp", "n_commune", "c_insee"]
                                        
                                        for k, v in props.items():
                                            if k in priority_keys:
                                                important_props[k] = v
                                            else:
                                                other_props[k] = v
                                        
                                        # Utiliser des tabs au lieu d'expanders imbriqués
                                        tab1, tab2 = st.tabs(["Propriétés principales", "Propriétés détaillées"])
                                        
                                        with tab1:
                                            if important_props:
                                                df_important = pd.DataFrame(list(important_props.items()), 
                                                                        columns=["Propriété", "Valeur"])
                                                st.dataframe(df_important)
                                            else:
                                                st.info("Aucune propriété principale disponible")
                                        
                                        with tab2:
                                            if other_props:
                                                df_other = pd.DataFrame(list(other_props.items()), 
                                                                    columns=["Propriété", "Valeur"])
                                                st.dataframe(df_other)
                                            else:
                                                st.info("Aucune propriété détaillée disponible")
                            else:
                                st.warning(f"❌ Cette adresse n'est dans aucune zone PNA parmi les fichiers chargés")
                            
                            # Ajouter un bouton pour refaire une recherche
                            st.button("🔄 Faire une nouvelle recherche", on_click=reset_fields)
                        else:
                            st.error("Impossible de géocoder cette adresse")
        else:  # Mode Coordonnées
            # Utiliser les dernières coordonnées ou les valeurs par défaut si réinitialisation
            if st.session_state.reset_pressed:
                initial_lat = 43.6  # Centre approximatif de l'Occitanie
                initial_lon = 2.7
                st.session_state.reset_pressed = False  # Réinitialiser le flag
            else:
                initial_lat = st.session_state.last_lat
                initial_lon = st.session_state.last_lon
            
            st.write("Coordonnées en WGS84 (format GPS standard)")
            lat_col, lon_col = st.columns(2)
            with lat_col:
                lat = st.number_input("Latitude", value=initial_lat, format="%.6f")
            with lon_col:
                lon = st.number_input("Longitude", value=initial_lon, format="%.6f")
            
            # Option pour saisir en Lambert93
            use_lambert93 = st.checkbox("Saisir en Lambert 93")
            
            if use_lambert93:
                l93_col1, l93_col2 = st.columns(2)
                with l93_col1:
                    x_l93 = st.number_input("X (Lambert 93)", value=650000.0, format="%.1f")
                with l93_col2:
                    y_l93 = st.number_input("Y (Lambert 93)", value=6250000.0, format="%.1f")
                
                # Convertir Lambert93 en WGS84 pour l'affichage
                point_l93 = gpd.GeoDataFrame(geometry=[Point(x_l93, y_l93)], crs="EPSG:2154")
                point_wgs84 = point_l93.to_crs("EPSG:4326")
                lat = point_wgs84.geometry[0].y
                lon = point_wgs84.geometry[0].x
                
                # Mise à jour des valeurs affichées
                st.write(f"Coordonnées équivalentes en WGS84: {lat:.6f}, {lon:.6f}")
            else:
                # Convertir WGS84 en Lambert93 pour l'analyse
                point_wgs84 = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")
                point_l93 = point_wgs84.to_crs("EPSG:2154")
                x_l93, y_l93 = point_l93.geometry[0].x, point_l93.geometry[0].y
            
            # Bouton de vérification pour les coordonnées
            check_coords = st.button("Vérifier les coordonnées")
            
            if check_coords:
                if not all_data_sources:
                    st.error("Veuillez d'abord charger au moins un fichier GeoJSON")
                else:
                    # Mettre à jour les coordonnées en session state
                    st.session_state.last_lat = lat
                    st.session_state.last_lon = lon
                    
                    # Effacer le placeholder et créer un nouveau conteneur
                    results_placeholder.empty()
                    
                    with results_placeholder.container():
                        st.write(f"Coordonnées WGS84: {lat:.6f}, {lon:.6f}")
                        st.write(f"Coordonnées Lambert 93: {x_l93:.2f}, {y_l93:.2f}")
                        
                        # Vérification PNA
                        in_pna, results = is_in_pna(lat, lon, x_l93, y_l93, all_data_sources)
                        
                        # Afficher le résultat textuel
                        if in_pna:
                            st.success(f"✅ Ces coordonnées sont situées dans {len(results)} zone(s) PNA")
                            
                            # Créer un tableau avec tous les résultats
                            result_data = []
                            for i, props in enumerate(results, 1):
                                pna_type = props.get("type_pna", "Inconnu")
                                file_source = props.get("fichier_source", "")
                                espece = props.get("n_espece", "Non spécifié")
                                enjeu = ""
                                
                                if pna_type == "Chiroptères":
                                    enjeu = props.get("enjeu_détaillé", "Indéterminé")
                                
                                result_data.append({
                                    "Zone": i,
                                    "Type PNA": pna_type, 
                                    "Espèce": espece,
                                    "Enjeu": enjeu,
                                    "Fichier source": file_source
                                })
                            
                            # Afficher le tableau récapitulatif
                            st.subheader("Zones PNA détectées:")
                            st.dataframe(pd.DataFrame(result_data))
                            
                            # Affichage détaillé pour chaque zone
                            for i, props in enumerate(results, 1):
                                with st.expander(f"Détails de la zone {i} - {props.get('n_espece', 'Non spécifié')}"):
                                    # Filtrer et trier les propriétés pour plus de clarté
                                    important_props = {}
                                    other_props = {}
                                    
                                    # Définir les propriétés importantes à afficher en premier
                                    priority_keys = ["n_espece", "t_enjeux", "enjeu_détaillé", "type_pna", "richessesp", "n_commune", "c_insee"]
                                    
                                    for k, v in props.items():
                                        if k in priority_keys:
                                            important_props[k] = v
                                        else:
                                            other_props[k] = v
                                    
                                    # Utiliser des tabs au lieu d'expanders imbriqués
                                    tab1, tab2 = st.tabs(["Propriétés principales", "Propriétés détaillées"])
                                    
                                    with tab1:
                                        if important_props:
                                            df_important = pd.DataFrame(list(important_props.items()), 
                                                                    columns=["Propriété", "Valeur"])
                                            st.dataframe(df_important)
                                        else:
                                            st.info("Aucune propriété principale disponible")
                                    
                                    with tab2:
                                        if other_props:
                                            df_other = pd.DataFrame(list(other_props.items()), 
                                                                columns=["Propriété", "Valeur"])
                                            st.dataframe(df_other)
                                        else:
                                            st.info("Aucune propriété détaillée disponible")
                        else:
                            st.warning(f"❌ Ces coordonnées ne sont dans aucune zone PNA parmi les fichiers chargés")
                        
                        # Ajouter un bouton pour refaire une recherche
                        st.button("🔄 Faire une nouvelle recherche", on_click=reset_fields)

# Pied de page
st.markdown("---")
st.info("""Cette application vérifie si une adresse ou des coordonnées GPS sont situées dans une 
        zone de Plan National d'Actions (PNA). Supporte les fichiers GeoJSON.
        Vous pouvez charger plusieurs fichiers GeoJSON pour vérifier si un point est dans plusieurs types de zones PNA simultanément.
        Utilise l'API adresse.data.gouv.fr pour le géocodage.""")