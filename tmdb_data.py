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

def get_genres() -> dict:
    "Fetch genres"
    headers = _create_headers()
    url_genres = "https://api.themoviedb.org/3/genre/movie/list?language=en"
    res_genres = requests.get(url_genres, headers=headers)
    print(json.loads(res_genres.text))
    genre_dict = {genre['id']: genre['name'] for genre in json.loads(res_genres.text)['genres']}
    return genre_dict

def get_movies(genre_id: Optional[int] = None, min_vote: Optional[float] = None, provider: int = 8):
    "Fetch movies filtered by genre, minimum vote average, and provider"
    # Build the URL with filters
    headers = _create_headers()
    base_url = "https://api.themoviedb.org/3/discover/movie?include_adult=false&include_video=false&sort_by=vote_average.desc&vote_count.gte=1000&watch_region=NL"
    
    if genre_id is not None: base_url += f"&with_genres={genre_id}"
    if min_vote is not None: base_url += f"&vote_average.gte={min_vote}"
    base_url += f"&with_watch_providers={provider}"
    
    # Create genre lookup dictionary
    genre_lookup = get_genres()
    
    # Fetch all pages
    all_results = []
    page = 1
    total_pages = 1  # Will be updated after first request
    
    while page <= total_pages:
        url = f"{base_url}&page={page}"
        response = requests.get(url, headers=headers)
        data = json.loads(response.text)
        
        all_results.extend(data['results'])
        total_pages = data['total_pages']
        page += 1
    
    # Convert to dataframe
    df = pd.DataFrame(all_results)
    
    # Keep only needed columns
    if 'overview' in df.columns: df = df.rename(columns={'overview': 'description'})
    cols_to_keep = ['title', 'vote_average', 'genre_ids', 'release_date', 'description']
    df = df[cols_to_keep].copy()
    
    # Convert genre IDs to names
    df['genres'] = df['genre_ids'].apply(lambda ids: [genre_lookup[gid] for gid in ids])
    df = df.drop(columns=['genre_ids'])
    
    # Format data
    df['vote_average'] = df['vote_average'].round(1)
    df['release_date'] = pd.to_datetime(df['release_date']).dt.year
    
    # Sort by vote average (descending)
    df = df.sort_values('vote_average', ascending=False)
    
    return df

if __name__ == "__main__":
    df = get_movies()
    print(df['description'].head())
    