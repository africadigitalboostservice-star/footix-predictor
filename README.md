# ⚽ Footix AI Predictor

Backend de prédiction football avec vraies données via Football-Data.org, TheSportsDB et API-Football.

---

## 🚀 Déploiement sur Railway

### 1. Préparer le repo GitHub

```bash
git init
git add .
git commit -m "Initial commit — Footix AI Predictor"
git remote add origin https://github.com/TON_USER/footix-predictor.git
git push -u origin main
```

### 2. Créer le projet Railway

1. Aller sur [railway.app](https://railway.app)
2. **New Project → Deploy from GitHub repo**
3. Sélectionner ton repo `footix-predictor`
4. Railway détecte automatiquement le `Procfile`

### 3. Configurer les variables d'environnement

Dans Railway → ton service → onglet **Variables**, ajouter :

| Variable | Valeur | Obligatoire |
|---|---|---|
| `FOOTBALL_DATA_TOKEN` | ton token | ✅ Oui |
| `API_FOOTBALL_KEY` | ta clé RapidAPI | Non (optionnel) |

> **Obtenir un token Football-Data.org gratuit :**
> https://www.football-data.org/client/register

### 4. Vérifier le déploiement

Une fois le build terminé (badge vert), tester :

```bash
curl https://TON_APP.railway.app/health
```

---

## 📡 Endpoints API

### `GET /health`
Statut des APIs connectées.

```json
{
  "status": "ok",
  "apis": {
    "football_data": { "configured": true },
    "the_sports_db": { "configured": true },
    "api_football":  { "configured": false }
  }
}
```

---

### `GET /teams/<nom>`
Statistiques réelles d'une équipe.

```bash
curl https://TON_APP.railway.app/teams/Arsenal
```

---

### `POST /predict`
Prédiction complète d'un match.

```bash
curl -X POST https://TON_APP.railway.app/predict \
  -H "Content-Type: application/json" \
  -d '{
    "team1": "Arsenal",
    "team2": "Chelsea",
    "team1_home": true
  }'
```

**Réponse :**
```json
{
  "success": true,
  "match": "Arsenal vs Chelsea",
  "prediction": {
    "expected_goals": { "team1": 1.72, "team2": 1.18, "total": 2.90 },
    "probabilities": { "team1_win": 48.3, "draw": 25.1, "team2_win": 26.6 },
    "most_likely_score": "1-1",
    "markets": {
      "over_2_5":  { "prob": 54.2, "prediction": true },
      "over_3_5":  { "prob": 28.7, "prediction": false },
      "btts":      { "prob": 61.4, "prediction": true },
      "corners_over_8_5": { "prob": 67.1, "prediction": true }
    },
    "confidence": 90,
    "warnings": []
  }
}
```

---

### `POST /cache/clear`
Vider le cache (utile après une mise à jour des données).

---

## 🛠️ Test local

```bash
# Installer les dépendances
pip install -r requirements.txt

# Configurer les variables
cp .env.example .env
# Éditer .env avec ton token

# Lancer le serveur
python football_predictor_production.py
```

---

## 🏗️ Architecture

```
football_predictor_production.py
│
├── FootballDataAPI     → Football-Data.org (top ligues européennes)
├── SportsDBAPI         → TheSportsDB (toutes compétitions, gratuit)
├── APIFootballClient   → API-Football RapidAPI (stats avancées, optionnel)
│
├── TeamAnalyzer        → Agrège les données des 3 APIs, calcule les stats
│
└── MatchPredictor      → Modèle de Poisson, probabilités, marchés de paris
```

---

## 📊 Modèle de prédiction

Le prédicteur utilise un **modèle de Poisson** (standard en analyse football) :

- `λ1 = (attaque_t1 × défense_t2) / moyenne_ligue`
- `λ2 = (attaque_t2 × défense_t1) / moyenne_ligue`
- Ajustement par la **forme récente** des 5 derniers matchs
- **Avantage domicile** : +10% buts attendus pour l'équipe à domicile
- **Niveau de confiance** basé sur la qualité et la quantité des données réelles
