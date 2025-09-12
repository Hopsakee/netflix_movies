import requests
import json
import pandas as pd
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()
# url_pathe = "https://api.themoviedb.org/3/discover/movie?include_adult=false&include_video=false&page=1&sort_by=vote_average.desc&vote_count.gte=1000&watch_region=NL&with_watch_providers=71"
# url_providers = "https://api.themoviedb.org/3/watch/providers/movie?language=en-US&watch_region=nl"

def _create_headers():
    "Create headers"
    return {"accept": "application/json",
           "Authorization": f"Bearer {os.environ['TMDB_API_KEY']}"}

def get_genres_movies() -> dict:
    "Fetch genres for movies"
    headers = _create_headers()
    url_genres = "https://api.themoviedb.org/3/genre/movie/list?language=en"
    res_genres = requests.get(url_genres, headers=headers)
    genre_dict = {genre['id']: genre['name'] for genre in json.loads(res_genres.text)['genres']}
    return genre_dict

def get_genres_series() -> dict:
    "Fetch genres for series"
    headers = _create_headers()
    url_genres = "https://api.themoviedb.org/3/genre/tv/list?language=en"
    res_genres = requests.get(url_genres, headers=headers)
    genre_dict = {genre['id']: genre['name'] for genre in json.loads(res_genres.text)['genres']}
    return genre_dict

def get_movies(genre_ids: Optional[list[int]] = None, no_genre_ids: Optional[list[int]] = None, min_vote: Optional[float] = None, provider: int = 8):
    "Fetch movies filtered by genre, minimum vote average, and provider"
    headers = _create_headers()
    genre_dict = get_genres_movies()
    base_url = "https://api.themoviedb.org/3/discover/movie?include_adult=false&include_video=false&sort_by=vote_average.desc&vote_count.gte=1000&watch_region=NL"
    if genre_ids is not None and genre_ids != 0: genre_ids_str = '%2C'.join(map(str, genre_ids)); base_url += f"&with_genres={genre_ids_str}"
    if min_vote is not None: base_url += f"&vote_average.gte={min_vote}"
    if no_genre_ids is not None and no_genre_ids != 0: no_genre_ids_str = '%2C'.join(map(str, no_genre_ids)); base_url += f"&without_genres={no_genre_ids_str}"
    base_url += f"&with_watch_providers={provider}"
    all_results = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        url = f"{base_url}&page={page}"
        response = requests.get(url, headers=headers)
        data = json.loads(response.text)
        all_results.extend(data['results'])
        total_pages = data['total_pages']
        page += 1
    df = pd.DataFrame(all_results)

    if len(df) == 0:
        all_genres = set()
        df = pd.DataFrame(columns=['title', 'vote_average', 'release_date', 'genres', 'description'])
    else:
        if 'overview' in df.columns: df = df.rename(columns={'overview': 'description'})
        cols_to_keep = ['title', 'vote_average', 'genre_ids', 'release_date', 'description']
        df = df[cols_to_keep].copy()
        df['genres'] = df['genre_ids'].apply(lambda ids: [genre_dict[gid] for gid in ids])
        df = df.drop(columns=['genre_ids'])
        df['vote_average'] = df['vote_average'].round(1)
        df['release_date'] = pd.to_datetime(df['release_date']).dt.year
        df = df.sort_values('vote_average', ascending=False)[:30]

        all_genres = set()
        for genres_list in df['genres']: all_genres.update(genres_list)
    
    no_genres = {genre_dict[gid] for gid in no_genre_ids} if no_genre_ids else {}

    mv_filter = {"min_rating": min_vote, "incl_genres": all_genres, "excl_genres": no_genres, "filt_genres": genre_ids}
    print(len(df))


    return df, mv_filter

def get_series(genre_ids: Optional[list[int]] = None, no_genre_ids: Optional[list[int]] = None, min_vote: Optional[float] = None, provider: int = 8):
    "Fetch series filtered by genre, minimum vote average, and provider"
    headers = _create_headers()
    genre_dict = get_genres_series()
    base_url = "https://api.themoviedb.org/3/discover/tv?include_adult=false&include_video=false&sort_by=vote_average.desc&vote_count.gte=1000&watch_region=NL"
    if genre_ids is not None and genre_ids != 0: genre_ids_str = '%2C'.join(map(str, genre_ids)); base_url += f"&with_genres={genre_ids_str}"
    if min_vote is not None: base_url += f"&vote_average.gte={min_vote}"
    if no_genre_ids is not None and no_genre_ids != 0: no_genre_ids_str = '%2C'.join(map(str, no_genre_ids)); base_url += f"&without_genres={no_genre_ids_str}"
    base_url += f"&with_watch_providers={provider}"
    all_results = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        url = f"{base_url}&page={page}"
        response = requests.get(url, headers=headers)
        data = json.loads(response.text)
        all_results.extend(data['results'])
        total_pages = data['total_pages']
        page += 1
    df = pd.DataFrame(all_results)
    print(df.columns)

    if len(df) == 0:
        all_genres = set()
        df = pd.DataFrame(columns=['name', 'vote_average', 'first_air_date', 'genres', 'description'])
    else:
        if 'overview' in df.columns: df = df.rename(columns={'overview': 'description'})
        cols_to_keep = ['name', 'vote_average', 'genre_ids', 'first_air_date', 'description']
        df = df[cols_to_keep].copy()
        df['genres'] = df['genre_ids'].apply(lambda ids: [genre_dict[gid] for gid in ids])
        df = df.drop(columns=['genre_ids'])
        df['vote_average'] = df['vote_average'].round(1)
        df['release_date'] = pd.to_datetime(df['first_air_date']).dt.year
        df['title'] = df['name']
        df = df.sort_values('vote_average', ascending=False)[:30]

        all_genres = set()
        for genres_list in df['genres']: all_genres.update(genres_list)
    
    no_genres = {genre_dict[gid] for gid in no_genre_ids} if no_genre_ids else {}

    mv_filter = {"min_rating": min_vote, "incl_genres": all_genres, "excl_genres": no_genres, "filt_genres": genre_ids}
    print(len(df))


    return df, mv_filter

if __name__ == "__main__":
    df = get_movies()
    print(df['description'].head())
    