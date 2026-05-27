from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import random

app = FastAPI()

# Autoriser ton site Tiiny.site à communiquer avec Railway
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class MatchRequest(BaseModel):
    team1: str
    team2: str

def generer_force_equipe(nom_equipe: str):
    """Calcule une force unique basée sur les lettres du nom de l'équipe"""
    force = sum(ord(char) for char in nom_equipe) % 50 + 50  # Score entre 50 et 100
    return force

@app.post("/predict")
async def predict(request: MatchRequest):
    t1 = request.team1
    t2 = request.team2
    
    # Calcul de forces dynamiques et uniques selon les équipes saisies
    force1 = generer_force_equipe(t1)
    force2 = generer_force_equipe(t2)
    total_force = force1 + force2
    
    # Génération de probabilités uniques
    p1_win = int((force1 / total_force) * 100)
    p2_win = int((force2 / total_force) * 100)
    draw = 100 - p1_win - p2_win
    
    # Ajustement léger pour éviter les nuls trop parfaits à 33.3%
    if draw == 0 or draw > 50:
        draw = 30
        restant = 70
        p1_win = int(restant * (force1 / total_force))
        p2_win = 100 - p1_win - draw

    # Calcul des buts réalistes selon la force de l'équipe
    goals_t1 = 1 if force1 < 65 else (2 if force1 < 85 else 3)
    goals_t2 = 0 if force2 < 60 else (1 if force2 < 80 else 2)
    
    # Variation si les forces sont très proches pour créer des scores variés (ex: 2-2, 0-0)
    if abs(force1 - force2) < 5:
        goals_t1 = goals_t2 = (force1 % 3)

    confidence = 65 + (total_force % 25)
    both_score = "true" if (goals_t1 > 0 and goals_t2 > 0) else "false"
    over_25 = "true" if (goals_t1 + goals_t2 > 2) else "false"

    return {
        "success": True,
        "match": f"{t1} vs {t2}",
        "prediction": {
            "analysis_timestamp": datetime.now().isoformat(),
            "confidence_level": confidence,
            "predicted_score": [goals_t1, goals_t2],
            "probabilities": {
                "team1_win": p1_win,
                "draw": draw,
                "team2_win": p2_win
            },
            "specialized_predictions": {
                "both_teams_score": {
                    "prediction": both_score == "true",
                    "confidence": 70 + (force1 % 20)
                },
                "over_2_5_goals": {
                    "prediction": over_25 == "true",
                    "confidence": 60 + (force2 % 25)
                }
            }
        }
    }
