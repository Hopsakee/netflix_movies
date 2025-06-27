from fastcore.all import *
from tmdb_data import get_movies
import os
from fasthtml.oauth import OAuth
from fasthtml.common import *
from fasthtml.oauth import GitHubAppClient, GoogleAppClient
from dotenv import load_dotenv
load_dotenv()

# cli_gh = GitHubAppClient(os.getenv("AUTH_CLIENT_ID"),
#                          os.getenv("AUTH_CLIENT_SECRET"))

cli_gg = GoogleAppClient(os.getenv("AUTH_CLIENT_ID_GG"),
                         os.getenv("AUTH_CLIENT_SECRET_GG"))

class Auth(OAuth):
    def get_auth(self, info, ident, session, state):
        if info.email in os.getenv('USERS_ALLOWED').split(','):
            return RedirectResponse('/', status_code=303)
        else:
            return Div(P("Not logged in want jij ben Hopsakee niet."))

app,rt = fast_app(live=True, hdrs=[
    # Include Pico CSS for nice default styling
    Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"),
    # Add some custom styles
    Style("""
        .movie-table {
            width: 100%;
            border-collapse: collapse;
            margin: 1rem 0;
            font-size: 0.9em;
            box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
            table-layout: fixed;
        }
        .movie-table th.title-col {
            width: 30%;
        }
        .movie-table th.rating-col {
            width: 5%;
        }
        .movie-table th.year-col {
            width: 5%;
        }
        .movie-table th.genre-col {
            width: 10%;
        }
        .movie-table th.desc-col {
            width: 50%;
        }
        .movie-table thead tr {
            background-color: #2c3e50;
            color: #ffffff;
            text-align: left;
        }
        .movie-table th,
        .movie-table td {
            padding: 10px 12px;
        }
        .movie-table tbody tr {
            border-bottom: 1px solid #dddddd;
        }
        .movie-table tbody tr:nth-of-type(even) {
            background-color: #f3f3f3;
        }
        .movie-table tbody tr:last-of-type {
            border-bottom: 2px solid #2c3e50;
        }
        .movie-table tbody tr:hover {
            background-color: #e1f5fe;
            cursor: pointer;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1rem;
        }
        .header {
            margin: 2rem 0;
            text-align: center;
        }
        .genre-tag {
            display: inline-block;
            background: #e0e0e0;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.8em;
            margin: 2px;
        }
    """)
])

# oauth_gh = Auth(app, cli_gh)
oauth_gg = Auth(app, cli_gg)

def create_movie_table(df):
    "Create a styled HTML table from the movies dataframe"
    # Create table header with specific column classes
    thead = Thead(Tr(
        Th('Title'),
        Th('Rating'),
        Th('Year'),
        Th('Genres', cls='genre-col'),
        Th('Description', cls='desc-col')
    ))
    
    # Create table rows
    rows = []
    for _, row in df.iterrows():
        # Format genres as stacked tags
        genres = Div(
            *[Div(g, cls="genre-tag") for g in row['genres']],
            style="display: flex; flex-direction: column; align-items: flex-start; gap: 4px;"
        )
        
        # Create table row
        rows.append(Tr(
            Td(row['title'], style="font-weight: bold"),
            Td(f"⭐ {row['vote_average']}"),
            Td(row['release_date']),
            Td(genres, style="padding: 8px"),
            Td(row['description'], style="white-space: normal; text-overflow: clip;"),
            _class="movie-row"
        ))
    
    # Combine everything into a table
    return Table(
        thead,
        Tbody(*rows),
        cls="movie-table",
    )



@rt
def index(auth):
    return Titled("🎬 Netflix Movies",
        Div(P("Kies welke lijst je wil zien", style="color: #666; margin-top: 0"),
            Div(
                Button("Best movies", hx_get=best.to(min_vote=7.8), hx_target="#index", hx_swap="innerHTML", hx_push_url="true", style="background-color: #007bff; color: white; padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; margin-right: 10px;"),
                Button("Action movies", hx_get=action.to(genre_id=28, min_vote=7), hx_target="#index", hx_swap="innerHTML", hx_push_url="true", style="background-color: #28a745; color: white; padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px;"),
                style="display: flex; justify-content: center;"
            )
        ),
        A('Log out', href='/logout'),
        id="index",
    )

@rt
def login(req):
    return Div(P("Je bent niet ingelogd."), 
        # A(P('Log in with GitHub'), href=oauth_gh.login_link(req)))
        A(P('Log in with Google'), href=oauth_gg.login_link(req)))

@rt
def action(genre_id: int = 28, min_vote: int = 7):
    try:
        # Get movies data with action genre (28) and minimum rating of 7
        df = get_movies(genre_id=genre_id, min_vote=min_vote)
        
        # Create the page with the movie table
        return Div(Titled("Netflix Action Movies", hx_get=index.to(), hx_target="#index", hx_swap="innerHTML", hx_push_url="true", style="text-decoration: underline;"),
            Div(
                Header(
                    H1("🎬 Top Action Movies", style="margin-bottom: 0.5rem"),
                    P(f"Sorted by rating, filtered for action movies with rating ≥ {min_vote}/10", 
                      style="color: #666; margin-top: 0"),
                    cls="header"
                ),
                create_movie_table(df),
                cls="container"
            )
        )
    except Exception as e:
        return Div(
            H1("Error", style="color: #dc3545"),
            P(f"An error occurred: {str(e)}", style="color: #6c757d"),
            cls="container"
        )

@rt
def best(min_vote: float = 7.8):
    try:
        # Get movies data with minimum rating of 7
        df = get_movies(min_vote=min_vote)
        
        # Create the page with the movie table
        return Div(Titled("Netflix Best Movies", hx_get=index.to(), hx_target="#index", hx_swap="innerHTML", hx_push_url="true", style="text-decoration: underline;"),
            Div(
                Header(
                    H1("🎬 Top Best Movies", style="margin-bottom: 0.5rem"),
                    P(f"Sorted by rating, filtered for movies with rating ≥ {min_vote}/10", 
                      style="color: #666; margin-top: 0"),
                    cls="header"
                ),
                create_movie_table(df),
                cls="container"
            )
        )
    except Exception as e:
        return Div(
            H1("Error", style="color: #dc3545"),
            P(f"An error occurred: {str(e)}", style="color: #6c757d"),
            cls="container"
        )

if __name__ == "__main__":
    serve()