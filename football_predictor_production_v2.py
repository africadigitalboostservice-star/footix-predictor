#!/usr/bin/env python3
"""
FOOTIX AI PREDICTOR - VERSION PRODUCTION V2
Backend Flask avec Football-Data.org et OpenAI Reasoning
"""

import os
import requests
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app)

class ProductionFootballPredictor:
    """Prédicteur football avec Football-Data.org et OpenAI Reasoning"""

    def __init__(self):
        self.football_data_token = os.getenv('FOOTBALL_DATA_TOKEN')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        
        self.apis = {
            'football_data': {
                'base_url': 'https://api.football-data.org/v4',
                'headers': {
                    'X-Auth-Token': self.football_data_token
                },
                'rate_limit': 10
            }
        }

        self.openai_client = OpenAI(api_key=self.openai_api_key, base_url='https://api.openai.com/v1') if self.openai_api_key else None
        self.last_request_time = {}
        self.cache = {}

    def _make_request(self, api_name: str, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Requête API avec rate limiting et cache"""
        cache_key = f"{api_name}_{endpoint}_{str(params)}"

        if cache_key in self.cache:
            cached_time, cached_data = self.cache[cache_key]
            if time.time() - cached_time < 3600:  # Cache 1 heure pour les données foot
                return cached_data

        api_config = self.apis.get(api_name)
        if not api_config:
            return None

        # Rate limiting simple
        now = time.time()
        if api_name in self.last_request_time:
            time_diff = now - self.last_request_time[api_name]
            min_interval = 60 / api_config['rate_limit']
            if time_diff < min_interval:
                time.sleep(min_interval - time_diff)

        self.last_request_time[api_name] = time.time()

        try:
            url = f"{api_config['base_url']}/{endpoint}"
            response = requests.get(
                url,
                headers=api_config['headers'],
                params=params or {},
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                self.cache[cache_key] = (time.time(), data)
                return data
            else:
                print(f"❌ Erreur API {api_name} {response.status_code}: {response.text}")
                return None
        except Exception as e:
            print(f"❌ Erreur réseau {api_name}: {e}")
            return None

    def get_team_data(self, team_name: str) -> Dict:
        """Rechercher une équipe et ses stats récentes"""
        # On cherche d'abord dans les compétitions majeures
        competitions = [2021, 2014, 2019, 2002, 2015, 2017] # PL, PD, SA, BL, FL1, PPL
        
        for comp_id in competitions:
            teams_data = self._make_request('football_data', f'competitions/{comp_id}/teams')
            if teams_data and 'teams' in teams_data:
                for team in teams_data['teams']:
                    if team_name.lower() in team['name'].lower() or team['name'].lower() in team_name.lower():
                        # Récupérer les matchs récents
                        matches = self._make_request(
                            'football_data',
                            f'teams/{team["id"]}/matches',
                            {'status': 'FINISHED', 'limit': 10}
                        )
                        return {
                            'info': team,
                            'recent_matches': matches.get('matches', []) if matches else []
                        }
        return {"info": {"name": team_name}, "recent_matches": []}

    def get_ai_reasoning(self, team1_data: Dict, team2_data: Dict) -> str:
        """Utiliser OpenAI pour une analyse avec réflexion"""
        if not self.openai_client:
            return "Analyse IA non disponible (Clé manquante)."

        prompt = f"""
        En tant qu'expert en analyse de données footballistiques, analyse le match suivant :
        Équipe 1 : {team1_data['info']['name']}
        Équipe 2 : {team2_data['info']['name']}

        Données récentes Équipe 1 : {json.dumps(team1_data['recent_matches'][:5])}
        Données récentes Équipe 2 : {json.dumps(team2_data['recent_matches'][:5])}

        Fournis une analyse "Reasoning" (réflexion profonde) sur :
        1. La dynamique actuelle des deux équipes.
        2. Les points forts et faibles tactiques.
        3. Une prédiction précise du score et du résultat (1N2).
        4. Des conseils de paris (Over/Under, Buteurs potentiels).
        
        Réponds en Français avec un ton professionnel et analytique.
        """

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini", # Utilisation de gpt-4o-mini pour une analyse rapide et efficace
                messages=[
                    {"role": "system", "content": "Tu es un expert en pronostics footballistiques de haut niveau."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Erreur lors de l'analyse IA : {str(e)}"

    def predict(self, team1: str, team2: str) -> Dict:
        """Processus complet de prédiction"""
        t1_data = self.get_team_data(team1)
        t2_data = self.get_team_data(team2)
        
        ai_analysis = self.get_ai_reasoning(t1_data, t2_data)
        
        return {
            "match": f"{team1} vs {team2}",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ai_analysis": ai_analysis,
            "data_status": "OK" if t1_data['recent_matches'] and t2_data['recent_matches'] else "Données limitées"
        }

predictor = ProductionFootballPredictor()

@app.route('/predict', methods=['POST'])
def predict_endpoint():
    data = request.json
    team1 = data.get('team1')
    team2 = data.get('team2')
    if not team1 or not team2:
        return jsonify({"error": "Veuillez fournir team1 et team2"}), 400
    
    result = predictor.predict(team1, team2)
    return jsonify(result)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "football_data_connected": bool(os.getenv('FOOTBALL_DATA_TOKEN')),
        "openai_connected": bool(os.getenv('OPENAI_API_KEY'))
    })

if __name__ == '__main__':
    # Chargement manuel des variables d'env pour le test
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
