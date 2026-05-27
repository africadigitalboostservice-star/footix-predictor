#!/usr/bin/env python3
"""
FOOTIX AI PREDICTOR - VERSION PRODUCTION RÉELLE
Backend Flask avec vraies APIs footballistiques et analyses complètes

Dépendances: pip install flask flask-cors requests
APIs utilisées:
  - Football-Data.org  → token gratuit sur https://www.football-data.org/client/register
  - TheSportsDB        → gratuit, sans token
  - API-Football (RapidAPI) → optionnel, plan gratuit disponible

Variables d'environnement requises:
  FOOTBALL_DATA_TOKEN  → token Football-Data.org (obligatoire pour les top ligues)
  API_FOOTBALL_KEY     → clé RapidAPI API-Football (optionnel, enrichit les données)
"""

import os
import time
import json
import math
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from functools import lru_cache

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("footix")

FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "")
API_FOOTBALL_KEY    = os.getenv("API_FOOTBALL_KEY", "")

# IDs des compétitions Football-Data.org
COMPETITION_IDS = {
    "Premier League":  2021,
    "La Liga":         2014,
    "Bundesliga":      2002,
    "Serie A":         2019,
    "Ligue 1":         2015,
    "Champions League":2001,
    "World Cup":       2000,
    "Euro":            2018,
}

# IDs des ligues API-Football (RapidAPI)
API_FOOTBALL_LEAGUE_IDS = {
    "Premier League":  39,
    "La Liga":         140,
    "Bundesliga":      78,
    "Serie A":         135,
    "Ligue 1":         61,
    "Champions League":2,
    "World Cup":       1,
    "Eredivisie":      88,
    "Primeira Liga":   94,
}

CACHE_TTL_SECONDS = 600   # 10 minutes
MAX_RECENT_MATCHES = 10   # matchs récents analysés par équipe


# ─────────────────────────────────────────────
# CACHE LÉGER
# ─────────────────────────────────────────────

class SimpleCache:
    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self.ttl = ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry and (time.time() - entry[0]) < self.ttl:
            return entry[1]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time(), value)

    def clear(self) -> None:
        self._store.clear()


cache = SimpleCache()


# ─────────────────────────────────────────────
# COUCHE D'ACCÈS AUX APIs
# ─────────────────────────────────────────────

class FootballDataAPI:
    """
    Client pour Football-Data.org
    Documentation: https://docs.football-data.org/
    Rate limit: 10 req/min (plan gratuit)
    """

    BASE = "https://api.football-data.org/v4"
    _last_call = 0.0
    MIN_INTERVAL = 6.5  # secondes entre chaque appel (≈ 9 req/min)

    def __init__(self, token: str):
        self.token = token
        self.available = bool(token)

    def _get(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        if not self.available:
            log.warning("Football-Data.org: token manquant")
            return None

        cache_key = f"fd_{endpoint}_{json.dumps(params or {}, sort_keys=True)}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Rate limiting
        elapsed = time.time() - FootballDataAPI._last_call
        if elapsed < self.MIN_INTERVAL:
            time.sleep(self.MIN_INTERVAL - elapsed)
        FootballDataAPI._last_call = time.time()

        url = f"{self.BASE}/{endpoint}"
        try:
            resp = requests.get(
                url,
                headers={"X-Auth-Token": self.token},
                params=params or {},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                cache.set(cache_key, data)
                return data
            elif resp.status_code == 429:
                log.warning("Football-Data.org: rate limit atteint, attente 60s")
                time.sleep(60)
                return None
            else:
                log.error("Football-Data.org %s → HTTP %d: %s", url, resp.status_code, resp.text[:200])
                return None
        except requests.RequestException as e:
            log.error("Football-Data.org erreur réseau: %s", e)
            return None

    def search_team(self, name: str) -> Optional[Dict]:
        """Cherche une équipe par nom dans toutes les compétitions configurées."""
        for comp_name, comp_id in COMPETITION_IDS.items():
            data = self._get(f"competitions/{comp_id}/teams")
            if not data:
                continue
            for team in data.get("teams", []):
                if _names_match(name, team.get("name", "")) or _names_match(name, team.get("shortName", "")):
                    log.info("Équipe '%s' trouvée dans %s (Football-Data.org)", team["name"], comp_name)
                    return team
        return None

    def get_team_matches(self, team_id: int, n: int = MAX_RECENT_MATCHES) -> List[Dict]:
        """Récupère les n derniers matchs terminés d'une équipe."""
        data = self._get(f"teams/{team_id}/matches", {"status": "FINISHED", "limit": n + 5})
        if not data:
            return []
        matches = [m for m in data.get("matches", []) if m.get("status") == "FINISHED"]
        return matches[:n]

    def get_team_standings(self, team_id: int) -> Optional[Dict]:
        """Récupère le classement actuel de l'équipe (si disponible)."""
        for comp_id in COMPETITION_IDS.values():
            data = self._get(f"competitions/{comp_id}/standings")
            if not data:
                continue
            for table_entry in data.get("standings", []):
                for row in table_entry.get("table", []):
                    if row.get("team", {}).get("id") == team_id:
                        return row
        return None


class SportsDBAPI:
    """
    Client pour TheSportsDB (gratuit, sans token)
    Documentation: https://www.thesportsdb.com/api.php
    """

    BASE = "https://www.thesportsdb.com/api/v1/json/3"

    def _get(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        cache_key = f"sdb_{endpoint}_{json.dumps(params or {}, sort_keys=True)}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{self.BASE}/{endpoint}"
        try:
            resp = requests.get(url, params=params or {}, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                cache.set(cache_key, data)
                return data
            else:
                log.error("TheSportsDB %s → HTTP %d", url, resp.status_code)
                return None
        except requests.RequestException as e:
            log.error("TheSportsDB erreur réseau: %s", e)
            return None

    def search_team(self, name: str) -> Optional[Dict]:
        data = self._get("searchteams.php", {"t": name})
        teams = data.get("teams") if data else None
        if teams:
            return teams[0]
        return None

    def get_last_matches(self, team_id: str, n: int = MAX_RECENT_MATCHES) -> List[Dict]:
        data = self._get("eventslast.php", {"id": team_id})
        results = data.get("results") if data else None
        if not results:
            return []
        return results[:n]

    def get_next_matches(self, team_id: str) -> List[Dict]:
        data = self._get("eventsnext.php", {"id": team_id})
        events = data.get("events") if data else None
        return events or []


class APIFootballClient:
    """
    Client pour API-Football (RapidAPI) — optionnel
    Fournit des statistiques avancées: possession, tirs, corners, cartons
    Documentation: https://www.api-football.com/documentation-v3
    """

    BASE = "https://api-football-v1.p.rapidapi.com/v3"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.available = bool(api_key)
        self.headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com",
        }

    def _get(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        if not self.available:
            return None

        cache_key = f"apif_{endpoint}_{json.dumps(params or {}, sort_keys=True)}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{self.BASE}/{endpoint}"
        try:
            resp = requests.get(url, headers=self.headers, params=params or {}, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                cache.set(cache_key, data)
                return data
            else:
                log.error("API-Football %s → HTTP %d", url, resp.status_code)
                return None
        except requests.RequestException as e:
            log.error("API-Football erreur réseau: %s", e)
            return None

    def get_team_statistics(self, team_id: int, league_id: int, season: int) -> Optional[Dict]:
        data = self._get("teams/statistics", {
            "team": team_id,
            "league": league_id,
            "season": season,
        })
        return data.get("response") if data else None

    def search_team(self, name: str) -> Optional[Dict]:
        data = self._get("teams", {"search": name})
        response = data.get("response") if data else None
        if response:
            return response[0].get("team")
        return None


# ─────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────

def _names_match(a: str, b: str) -> bool:
    """Correspondance souple entre deux noms d'équipes."""
    def clean(s: str) -> str:
        return s.lower().strip().replace(" ", "").replace("-", "").replace(".", "").replace("'", "")
    ca, cb = clean(a), clean(b)
    return ca == cb or ca in cb or cb in ca


def _form_to_points(form: List[str]) -> float:
    """Convertit une liste ['W','D','L',...] en facteur de forme entre 0.75 et 1.25."""
    if not form:
        return 1.0
    recent = form[-5:]
    pts = sum(3 if r == "W" else 1 if r == "D" else 0 for r in recent)
    max_pts = len(recent) * 3
    ratio = pts / max_pts if max_pts > 0 else 0.5
    return 0.75 + ratio * 0.5


def _current_season() -> int:
    now = datetime.now()
    return now.year if now.month >= 7 else now.year - 1


# ─────────────────────────────────────────────
# MOTEUR D'ANALYSE
# ─────────────────────────────────────────────

class TeamAnalyzer:
    """
    Analyse complète d'une équipe à partir des données de plusieurs APIs.
    Priorité: Football-Data.org > API-Football > TheSportsDB
    """

    def __init__(self, fd_api: FootballDataAPI, sdb_api: SportsDBAPI, apif: APIFootballClient):
        self.fd  = fd_api
        self.sdb = sdb_api
        self.apif = apif

    def analyze(self, team_name: str) -> Dict:
        """Point d'entrée: retourne un dictionnaire de stats normalisé."""
        log.info("Analyse de l'équipe: %s", team_name)

        # ── Tentative Football-Data.org ──────────────────────────────────────
        fd_team = self.fd.search_team(team_name)
        if fd_team:
            matches = self.fd.get_team_matches(fd_team["id"])
            if matches:
                stats = self._parse_fd_matches(fd_team, matches)

                # Enrichissement via classement
                standing = self.fd.get_team_standings(fd_team["id"])
                if standing:
                    stats["ranking"] = {
                        "position": standing.get("position"),
                        "points":   standing.get("points"),
                        "played":   standing.get("playedGames"),
                    }

                # Enrichissement via API-Football (stats avancées)
                if self.apif.available:
                    self._enrich_with_apif(stats, team_name)

                log.info("Stats Football-Data.org pour %s: %s matchs analysés", team_name, stats["matches_analyzed"])
                return stats

        # ── Tentative TheSportsDB ────────────────────────────────────────────
        sdb_team = self.sdb.search_team(team_name)
        if sdb_team:
            team_id = sdb_team.get("idTeam")
            matches = self.sdb.get_last_matches(team_id) if team_id else []
            stats = self._parse_sdb_matches(sdb_team, matches)

            if self.apif.available:
                self._enrich_with_apif(stats, team_name)

            log.info("Stats TheSportsDB pour %s: %s matchs analysés", team_name, stats["matches_analyzed"])
            return stats

        # ── Aucune donnée trouvée ────────────────────────────────────────────
        log.warning("Aucune donnée trouvée pour '%s'", team_name)
        return self._empty_stats(team_name)

    # ── Parsers ──────────────────────────────────────────────────────────────

    def _parse_fd_matches(self, team_info: Dict, matches: List[Dict]) -> Dict:
        team_id   = team_info["id"]
        team_name = team_info["name"]

        goals_scored    = []
        goals_conceded  = []
        form            = []
        clean_sheets    = 0
        home_wins = away_wins = 0

        for m in matches:
            home_id = m["homeTeam"]["id"]
            score   = m.get("score", {}).get("fullTime", {})
            h_goals = score.get("home")
            a_goals = score.get("away")

            if h_goals is None or a_goals is None:
                continue

            is_home = (home_id == team_id)
            gf = h_goals if is_home else a_goals
            ga = a_goals if is_home else h_goals

            goals_scored.append(gf)
            goals_conceded.append(ga)

            if gf > ga:
                form.append("W")
                if is_home: home_wins += 1
                else:       away_wins += 1
            elif gf < ga:
                form.append("L")
            else:
                form.append("D")

            if ga == 0:
                clean_sheets += 1

        n = len(goals_scored)
        if n == 0:
            return self._empty_stats(team_name)

        return {
            "team_name":        team_name,
            "data_source":      "Football-Data.org",
            "data_quality":     "high",
            "matches_analyzed": n,
            "form":             form,
            "goals_scored":     goals_scored,
            "goals_conceded":   goals_conceded,
            "avg_goals_scored":    round(sum(goals_scored) / n, 3),
            "avg_goals_conceded":  round(sum(goals_conceded) / n, 3),
            "wins":   form.count("W"),
            "draws":  form.count("D"),
            "losses": form.count("L"),
            "win_pct":          round(form.count("W") / n * 100, 1),
            "clean_sheet_pct":  round(clean_sheets / n * 100, 1),
            "home_wins":        home_wins,
            "away_wins":        away_wins,
            "btts_pct":         round(
                sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf > 0 and ga > 0) / n * 100, 1
            ),
            "over_2_5_pct":     round(
                sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf + ga > 2.5) / n * 100, 1
            ),
            "ranking":          None,
            "advanced":         {},
        }

    def _parse_sdb_matches(self, team_info: Dict, matches: List[Dict]) -> Dict:
        team_name = team_info.get("strTeam", "Unknown")
        team_id_str = str(team_info.get("idTeam", ""))

        goals_scored   = []
        goals_conceded = []
        form           = []

        for m in matches:
            home_id = str(m.get("idHomeTeam", ""))
            try:
                h_goals = int(m.get("intHomeScore") or -1)
                a_goals = int(m.get("intAwayScore") or -1)
            except (ValueError, TypeError):
                continue

            if h_goals < 0 or a_goals < 0:
                continue

            is_home = (home_id == team_id_str)
            gf = h_goals if is_home else a_goals
            ga = a_goals if is_home else h_goals

            goals_scored.append(gf)
            goals_conceded.append(ga)

            if gf > ga:   form.append("W")
            elif gf < ga: form.append("L")
            else:          form.append("D")

        n = len(goals_scored)
        if n == 0:
            return self._empty_stats(team_name, source="TheSportsDB", quality="low")

        return {
            "team_name":        team_name,
            "data_source":      "TheSportsDB",
            "data_quality":     "medium",
            "matches_analyzed": n,
            "form":             form,
            "goals_scored":     goals_scored,
            "goals_conceded":   goals_conceded,
            "avg_goals_scored":    round(sum(goals_scored) / n, 3),
            "avg_goals_conceded":  round(sum(goals_conceded) / n, 3),
            "wins":   form.count("W"),
            "draws":  form.count("D"),
            "losses": form.count("L"),
            "win_pct":          round(form.count("W") / n * 100, 1),
            "clean_sheet_pct":  round(
                sum(1 for ga in goals_conceded if ga == 0) / n * 100, 1
            ),
            "home_wins":        None,
            "away_wins":        None,
            "btts_pct":         round(
                sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf > 0 and ga > 0) / n * 100, 1
            ),
            "over_2_5_pct":     round(
                sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf + ga > 2.5) / n * 100, 1
            ),
            "ranking":          None,
            "advanced":         {},
        }

    def _enrich_with_apif(self, stats: Dict, team_name: str) -> None:
        """Ajoute les stats avancées depuis API-Football si disponible."""
        apif_team = self.apif.search_team(team_name)
        if not apif_team:
            return

        team_id = apif_team.get("id")
        season  = _current_season()

        for league_id in API_FOOTBALL_LEAGUE_IDS.values():
            team_stats = self.apif.get_team_statistics(team_id, league_id, season)
            if not team_stats:
                continue

            fixtures = team_stats.get("fixtures", {})
            goals    = team_stats.get("goals", {})
            cards    = team_stats.get("cards", {})

            stats["advanced"] = {
                "league_played":   fixtures.get("played", {}).get("total", 0),
                "avg_shots":       team_stats.get("shots", {}).get("total", {}).get("average"),
                "avg_possession":  team_stats.get("possession", {}).get("avg"),
                "yellow_per_game": _cards_per_game(cards.get("yellow", {}), fixtures),
                "red_per_game":    _cards_per_game(cards.get("red", {}), fixtures),
            }
            stats["data_source"] += " + API-Football"
            log.info("Stats avancées API-Football ajoutées pour %s", team_name)
            break

    @staticmethod
    def _empty_stats(team_name: str, source: str = "none", quality: str = "none") -> Dict:
        return {
            "team_name":        team_name,
            "data_source":      source,
            "data_quality":     quality,
            "matches_analyzed": 0,
            "form":             [],
            "goals_scored":     [],
            "goals_conceded":   [],
            "avg_goals_scored":    None,
            "avg_goals_conceded":  None,
            "wins": 0, "draws": 0, "losses": 0,
            "win_pct": None, "clean_sheet_pct": None,
            "home_wins": None, "away_wins": None,
            "btts_pct": None, "over_2_5_pct": None,
            "ranking": None, "advanced": {},
        }


def _cards_per_game(card_data: Dict, fixtures: Dict) -> Optional[float]:
    """Calcule le nombre moyen de cartons par match."""
    total = 0
    for minute_range, counts in card_data.items():
        if isinstance(counts, dict):
            total += counts.get("total") or 0
    played = fixtures.get("played", {}).get("total") or 0
    if played == 0:
        return None
    return round(total / played, 2)


# ─────────────────────────────────────────────
# MOTEUR DE PRÉDICTION
# ─────────────────────────────────────────────

class MatchPredictor:
    """
    Calcule les probabilités et les métriques de paris à partir
    des statistiques réelles des deux équipes.

    Modèle utilisé:
      - Distribution de Poisson pour les buts (modèle Dixon-Coles simplifié)
      - Ajustement par la forme récente (facteur multiplicatif)
      - Ajustement par l'avantage domicile (si pertinent)
    """

    HOME_ADVANTAGE = 0.10  # +10% buts attendus pour l'équipe à domicile

    def predict(
        self,
        t1_stats: Dict,
        t2_stats: Dict,
        t1_is_home: bool = True,
    ) -> Dict:
        """
        t1_stats / t2_stats : dictionnaires retournés par TeamAnalyzer.analyze()
        t1_is_home          : True si t1 joue à domicile
        """
        # ── Buts attendus (lambda) ────────────────────────────────────────────
        λ1, λ2, confidence, warnings = self._compute_lambdas(t1_stats, t2_stats, t1_is_home)

        # ── Distribution de Poisson ───────────────────────────────────────────
        probs = self._poisson_match_probs(λ1, λ2, max_goals=8)

        t1_win_prob = sum(probs[i][j] for i in range(9) for j in range(9) if i > j)
        draw_prob   = sum(probs[i][i] for i in range(9))
        t2_win_prob = sum(probs[i][j] for i in range(9) for j in range(9) if i < j)

        # ── Métriques de paris ────────────────────────────────────────────────
        over_2_5 = self._over_x5_prob(λ1, λ2, 2)
        over_3_5 = self._over_x5_prob(λ1, λ2, 3)
        btts_prob = self._btts_prob(λ1, λ2)

        # Cartons: utiliser la moyenne des deux équipes si disponible
        avg_yellow = self._avg_advanced(t1_stats, t2_stats, "yellow_per_game")
        expected_cards = avg_yellow * 2 if avg_yellow else None
        cards_over_4_5 = (
            round(min(0.95, max(0.40, (expected_cards - 4.5) * 0.2 + 0.55)), 3)
            if expected_cards
            else None
        )

        # Corners: corrélation empirique avec les buts attendus totaux
        expected_corners = (λ1 + λ2) * 3.5 + 4.0  # ~4 corners par but attendu
        corners_over_8_5_prob = self._poisson_over_prob(expected_corners, 8)

        # Score le plus probable
        best_score = max(
            ((i, j) for i in range(9) for j in range(9)),
            key=lambda ij: probs[ij[0]][ij[1]]
        )

        return {
            "expected_goals": {
                "team1": round(λ1, 3),
                "team2": round(λ2, 3),
                "total": round(λ1 + λ2, 3),
            },
            "probabilities": {
                "team1_win": round(t1_win_prob * 100, 1),
                "draw":      round(draw_prob   * 100, 1),
                "team2_win": round(t2_win_prob * 100, 1),
            },
            "most_likely_score": f"{best_score[0]}-{best_score[1]}",
            "score_probability": round(probs[best_score[0]][best_score[1]] * 100, 1),
            "markets": {
                "over_2_5":    {"prob": round(over_2_5 * 100, 1), "prediction": over_2_5 > 0.5},
                "over_3_5":    {"prob": round(over_3_5 * 100, 1), "prediction": over_3_5 > 0.5},
                "btts":        {"prob": round(btts_prob * 100, 1), "prediction": btts_prob > 0.5},
                "corners_over_8_5": {
                    "prob": round(corners_over_8_5_prob * 100, 1),
                    "expected": round(expected_corners, 1),
                    "prediction": corners_over_8_5_prob > 0.5,
                },
                "cards_over_4_5": {
                    "prob": round(cards_over_4_5 * 100, 1) if cards_over_4_5 else None,
                    "expected": round(expected_cards, 1) if expected_cards else None,
                    "prediction": (cards_over_4_5 or 0) > 0.5,
                },
            },
            "confidence": confidence,
            "warnings":   warnings,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_lambdas(
        self,
        t1: Dict,
        t2: Dict,
        t1_home: bool,
    ) -> Tuple[float, float, int, List[str]]:
        """
        Estime les buts attendus en croisant l'attaque de t1
        avec la défense de t2, et vice-versa.
        """
        warnings = []
        confidence = 90

        t1_attack  = t1.get("avg_goals_scored")
        t1_defense = t1.get("avg_goals_conceded")
        t2_attack  = t2.get("avg_goals_scored")
        t2_defense = t2.get("avg_goals_conceded")

        # Valeurs de repli basées sur la moyenne européenne
        LEAGUE_AVG = 1.4

        if t1_attack is None:
            t1_attack  = LEAGUE_AVG
            t1_defense = LEAGUE_AVG
            confidence -= 30
            warnings.append(f"Pas de données réelles pour {t1['team_name']} — moyenne utilisée")

        if t2_attack is None:
            t2_attack  = LEAGUE_AVG
            t2_defense = LEAGUE_AVG
            confidence -= 30
            warnings.append(f"Pas de données réelles pour {t2['team_name']} — moyenne utilisée")

        if t1.get("matches_analyzed", 0) < 4:
            confidence -= 10
            warnings.append(f"Peu de matchs disponibles pour {t1['team_name']}")

        if t2.get("matches_analyzed", 0) < 4:
            confidence -= 10
            warnings.append(f"Peu de matchs disponibles pour {t2['team_name']}")

        # Modèle d'interaction attaque / défense
        λ1 = (t1_attack * t2_defense / LEAGUE_AVG)
        λ2 = (t2_attack * t1_defense / LEAGUE_AVG)

        # Facteur de forme récente
        λ1 *= _form_to_points(t1.get("form", []))
        λ2 *= _form_to_points(t2.get("form", []))

        # Avantage domicile
        if t1_home:
            λ1 *= (1 + self.HOME_ADVANTAGE)
            λ2 *= (1 - self.HOME_ADVANTAGE * 0.5)

        # Bornes de sécurité
        λ1 = max(0.3, min(λ1, 5.0))
        λ2 = max(0.3, min(λ2, 5.0))

        return λ1, λ2, max(10, confidence), warnings

    @staticmethod
    def _poisson_prob(lam: float, k: int) -> float:
        return (lam ** k) * math.exp(-lam) / math.factorial(k)

    def _poisson_match_probs(self, λ1: float, λ2: float, max_goals: int = 8) -> List[List[float]]:
        """Matrice max_goals x max_goals des probabilités de scores."""
        p1 = [self._poisson_prob(λ1, k) for k in range(max_goals + 1)]
        p2 = [self._poisson_prob(λ2, k) for k in range(max_goals + 1)]
        return [[p1[i] * p2[j] for j in range(max_goals + 1)] for i in range(max_goals + 1)]

    def _over_x5_prob(self, λ1: float, λ2: float, x: int) -> float:
        """P(total buts > x.5) via Poisson."""
        prob_under = sum(
            self._poisson_prob(λ1, i) * self._poisson_prob(λ2, j)
            for i in range(x + 2) for j in range(x + 2)
            if i + j <= x
        )
        return 1 - prob_under

    def _btts_prob(self, λ1: float, λ2: float) -> float:
        """P(les deux équipes marquent) = P(t1>=1) * P(t2>=1)."""
        p_t1_scores = 1 - self._poisson_prob(λ1, 0)
        p_t2_scores = 1 - self._poisson_prob(λ2, 0)
        return p_t1_scores * p_t2_scores

    def _poisson_over_prob(self, lam: float, threshold: int) -> float:
        """P(X > threshold) pour une variable de Poisson de paramètre lam."""
        prob_under = sum(self._poisson_prob(lam, k) for k in range(threshold + 1))
        return 1 - prob_under

    @staticmethod
    def _avg_advanced(t1: Dict, t2: Dict, key: str) -> Optional[float]:
        v1 = t1.get("advanced", {}).get(key)
        v2 = t2.get("advanced", {}).get(key)
        values = [v for v in [v1, v2] if v is not None]
        return sum(values) / len(values) if values else None


# ─────────────────────────────────────────────
# INITIALISATION DES SERVICES
# ─────────────────────────────────────────────

fd_api   = FootballDataAPI(FOOTBALL_DATA_TOKEN)
sdb_api  = SportsDBAPI()
apif     = APIFootballClient(API_FOOTBALL_KEY)
analyzer = TeamAnalyzer(fd_api, sdb_api, apif)
predictor = MatchPredictor()


# ─────────────────────────────────────────────
# APPLICATION FLASK
# ─────────────────────────────────────────────

app = Flask(__name__)
CORS(app)


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "apis": {
            "football_data": {
                "configured": fd_api.available,
                "url": FootballDataAPI.BASE,
            },
            "the_sports_db": {
                "configured": True,
                "url": SportsDBAPI.BASE,
            },
            "api_football": {
                "configured": apif.available,
                "url": APIFootballClient.BASE,
            },
        },
    })


@app.route("/teams/<team_name>")
def get_team(team_name: str):
    try:
        stats = analyzer.analyze(team_name)
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        log.exception("Erreur /teams/%s", team_name)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/predict", methods=["POST"])
def predict():
    body = request.get_json(silent=True) or {}
    team1 = (body.get("team1") or "").strip()
    team2 = (body.get("team2") or "").strip()
    team1_home = bool(body.get("team1_home", True))

    if not team1 or not team2:
        return jsonify({
            "success": False,
            "error": "Les champs 'team1' et 'team2' sont obligatoires.",
            "example": {"team1": "Arsenal", "team2": "Chelsea", "team1_home": True},
        }), 400

    try:
        t1_stats = analyzer.analyze(team1)
        t2_stats = analyzer.analyze(team2)
        result   = predictor.predict(t1_stats, t2_stats, t1_is_home=team1_home)

        return jsonify({
            "success": True,
            "match":   f"{team1} vs {team2}",
            "team1_home": team1_home,
            "prediction": result,
            "team_stats": {
                "team1": {k: v for k, v in t1_stats.items() if k not in ("goals_scored", "goals_conceded")},
                "team2": {k: v for k, v in t2_stats.items() if k not in ("goals_scored", "goals_conceded")},
            },
            "generated_at": datetime.now().isoformat(),
        })

    except Exception as e:
        log.exception("Erreur /predict %s vs %s", team1, team2)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/cache/clear", methods=["POST"])
def clear_cache():
    cache.clear()
    return jsonify({"success": True, "message": "Cache vidé"})


@app.route("/")
def index():
    return jsonify({
        "service": "Footix AI Predictor",
        "version": "2.0.0-production",
        "endpoints": {
            "GET  /health":           "Statut des APIs",
            "GET  /teams/<name>":     "Statistiques réelles d'une équipe",
            "POST /predict":          "Prédiction d'un match (body JSON: team1, team2, team1_home)",
            "POST /cache/clear":      "Vider le cache",
        },
    })


# ─────────────────────────────────────────────
# POINT D'ENTRÉE
# ─────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))

    log.info("=" * 60)
    log.info("FOOTIX AI PREDICTOR — démarrage sur le port %d", port)
    log.info("Football-Data.org  : %s", "✅ configuré" if fd_api.available  else "❌ token manquant (export FOOTBALL_DATA_TOKEN=...)")
    log.info("API-Football       : %s", "✅ configuré" if apif.available     else "⚠️  optionnel (export API_FOOTBALL_KEY=...)")
    log.info("TheSportsDB        : ✅ toujours disponible (gratuit)")
    log.info("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=False)
