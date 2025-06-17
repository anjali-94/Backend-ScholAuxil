from flask import Flask, jsonify, request, render_template, redirect, url_for, send_from_directory
from flask_cors import CORS
import requests
import os
import PyPDF2
import fitz
import docx
import easyocr
import json
from werkzeug.utils import secure_filename
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
from utils.auth_utils import firebase_auth_required
import traceback
from flask import g
from utils.file_extractor import extract_text_from_pdf, extract_text_from_docx, extract_text_from_image, allowed_file
from dotenv import load_dotenv
load_dotenv()
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'pdf', 'txt', 'doc', 'docx'}  

app = Flask(__name__)
# CORS(app, resources={r"/api/*": {"origins": "https://schol-auxil-frontend-w4cr.vercel.app"}})
CORS(app)

API_KEY = os.getenv("OPENROUTER_API_KEY")
PLAGIARISM_CHECK_API_KEY = os.getenv("PLAGIARISM_API_KEY")
API_URL = "https://api.gowinston.ai/v2/plagiarism"
app.config['SECRET_KEY'] = os.environ['FLASK_SECRET_KEY']

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'research_repo.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db = SQLAlchemy(app)

# # Initialize Firebase Admin SDK from environment variable
# cred_data = os.environ.get('FIREBASE_CREDENTIALS_JSON')
# if cred_data:
#     firebase_cred = credentials.Certificate(json.loads(cred_data))
#     firebase_admin.initialize_app(firebase_cred)
# else:
#     raise Exception("FIREBASE_CREDENTIALS_JSON environment variable not set")


BIBIFY_API_BASE = 'https://api.bibify.org'

@app.route('/')
def home():
    return jsonify({"message": "ScholarAuxil API is running!"})


# --- Database Models ---
class Repository(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.String(128), nullable=False)
    papers = db.relationship('Paper', backref='repository', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'created_at': self.created_at.isoformat(),
            'user_id': self.user_id,
            'papers': [paper.to_dict() for paper in self.papers] if self.papers else []
        }

class Paper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    original_filename = db.Column(db.String(300), nullable=False)
    filepath = db.Column(db.String(300), nullable=False, unique=True)  # Stored filename (secure)
    notes = db.Column(db.Text, nullable=True)
    last_opened = db.Column(db.DateTime, nullable=True)
    last_page_seen = db.Column(db.Integer, nullable=True, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    repository_id = db.Column(db.Integer, db.ForeignKey('repository.id'), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'original_filename': self.original_filename,
            'filepath': self.filepath,
            'notes': self.notes,
            'last_opened': self.last_opened.isoformat() if self.last_opened else None,
            'last_page_seen': self.last_page_seen,
            'uploaded_at': self.uploaded_at.isoformat(),
            'repository_id': self.repository_id
        }

with app.app_context():
    instance_path = os.path.join(BASE_DIR, 'instance')
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
        print(f"Created directory: {instance_path}")

    db_path = os.path.join(instance_path, 'research_repo.db')
    if not os.path.exists(db_path):
        # This will create the empty SQLite file AND all tables
        db.create_all()
        print(f"Created new database and tables at {db_path}")
    else:
        print(f"Database already exists at {db_path}")

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
        print(f"Created directory: {UPLOAD_FOLDER}")

    print("Database tables checked/created. Necessary folders checked/created.")


# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        # Accept form data
        user_question = request.form.get('question')
        uploaded_file = request.files.get('file')
        uploaded_image = request.files.get('image')

        final_prompt = user_question or ""

        if uploaded_file:
            file_ext = uploaded_file.filename.rsplit('.', 1)[1].lower()
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(uploaded_file.filename))
            uploaded_file.save(filepath)

            # Extract text based on file type
            if file_ext == 'pdf':
                final_prompt += "\n" + extract_text_from_pdf(filepath)
            elif file_ext == 'docx':
                final_prompt += "\n" + extract_text_from_docx(filepath)
            elif file_ext in {'png', 'jpg', 'jpeg'}:
                final_prompt += "\n" + extract_text_from_image(filepath)

            os.remove(filepath)

        if uploaded_image:
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(uploaded_image.filename))
            uploaded_image.save(img_path)
            final_prompt += "\n" + extract_text_from_image(img_path)
            os.remove(img_path)

        if not final_prompt.strip():
            return jsonify({'error': 'No input provided.'}), 400

        # Now call the OpenRouter API
        payload = {
            "model": "microsoft/mai-ds-r1:free",
            "messages": [
                {"role": "user", "content": final_prompt}
            ]
        }

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

        response = requests.post('https://openrouter.ai/api/v1/chat/completions', headers=headers, json=payload)

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': 'Failed to fetch response from OpenRouter', 'details': response.text}), response.status_code

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    


# chatbot file upload 
@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No file selected for uploading'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        file_ext = filename.rsplit('.', 1)[1].lower()
        extracted_text = ""

        try:
            # Extract text based on file type
            if file_ext == 'pdf':
                extracted_text = extract_text_from_pdf(filepath)
            elif file_ext == 'docx':
                extracted_text = extract_text_from_docx(filepath)
            elif file_ext in {'png', 'jpg', 'jpeg'}:
                extracted_text = extract_text_from_image(filepath)
            else:
                return jsonify({'error': 'Unsupported file type'}), 400

            # Optional: Delete file after processing
            os.remove(filepath)

            return jsonify({'extractedText': extracted_text})

        except Exception as e:
            return jsonify({'error': f'Failed to process file: {str(e)}'}), 500
    else:
        return jsonify({'error': 'File type not allowed'}), 400

@app.route('/api/check-plagiarism', methods=['POST'])
def check_plagiarism():
    data = request.get_json()
    text = data.get('text', '')

    if not text:
        return jsonify({"error": "Text is required"}), 400

    payload = {
        "text": text,
        "language": "en",
        "country": "us"
    }

    headers = {
        "Authorization": f"Bearer {PLAGIARISM_CHECK_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(API_URL, json=payload, headers=headers)
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/repositories', methods=['GET'])
@firebase_auth_required
def get_repositories():
    """Returns all repositories as JSON."""
    user_id = g.user_id
    repositories = Repository.query.filter_by(user_id=user_id).order_by(Repository.created_at.desc()).all()
    return jsonify([repo.to_dict() for repo in repositories])

@app.route('/api/repository/new', methods=['POST'])
@firebase_auth_required
def create_repository():
    """Handles creation of a new repository via API."""
    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 400

    data = request.get_json()
    logging.info("Received JSON data: %s", data)

    repo_name = str(data.get('name', '')).strip()
    if not repo_name:
        return jsonify({'error': 'Repository name is required and cannot be empty'}), 400

    user_id = g.user_id  # Assuming this comes from your firebase_auth_required

    existing_repo = Repository.query.filter_by(name=repo_name, user_id=user_id).first()
    if existing_repo:
        return jsonify({'error': 'Repository with this name already exists'}), 409

    new_repo = Repository(name=repo_name, user_id=user_id)
    db.session.add(new_repo)
    db.session.commit()

    return jsonify(new_repo.to_dict()), 201

@app.route('/api/repository/<int:repo_id>', methods=['GET'])
@firebase_auth_required
def get_repository(repo_id):
    """Returns a specific repository with its papers as JSON."""
    repo = Repository.query.get_or_404(repo_id)
    return jsonify(repo.to_dict())

@app.route('/api/repository/<int:repo_id>/papers', methods=['POST'])
@firebase_auth_required
def upload_paper_to_repository(repo_id):
    return api_upload_paper(repo_id)

@app.route('/api/repository/<int:repo_id>/delete', methods=['POST','DELETE'])
@firebase_auth_required
def api_delete_repository(repo_id):
    """Deletes a repository and all its papers via API."""
    repo = Repository.query.get_or_404(repo_id)
    db.session.delete(repo)
    db.session.commit()
    return jsonify({'message': f'Repository "{repo.name}" and all its papers deleted successfully'}), 200

@app.route('/api/paper/upload/<int:repo_id>', methods=['POST'])
@firebase_auth_required
def api_upload_paper(repo_id):
    """Handles uploading a new paper to a specific repository via API."""
    repo = Repository.query.get_or_404(repo_id)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    title = request.form.get('title', '').strip()
    if not title:
        title = os.path.splitext(file.filename)[0]

    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400

    try:
        original_filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        filename = f"{timestamp}_{original_filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], str(repo_id))
        os.makedirs(filepath, exist_ok=True)
        file.save(os.path.join(filepath, filename))

        new_paper = Paper(
            title=title,
            original_filename=original_filename,
            filepath=os.path.join(str(repo_id), filename),
            repository_id=repo.id
        )
        db.session.add(new_paper)
        db.session.commit()
        return jsonify(new_paper.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/paper/<int:paper_id>', methods=['PUT', 'POST', 'GET'])
@firebase_auth_required
def api_paper_detail(paper_id):
    """Handles paper details via API."""
    paper = Paper.query.get_or_404(paper_id)
    
    if request.method == 'GET':
        # Update last_opened timestamp when the paper is accessed
        paper.last_opened = datetime.utcnow()
        db.session.commit()
        return jsonify(paper.to_dict())
    
    elif request.method == 'PUT':
        data = request.get_json()
        if 'notes' in data:
            paper.notes = data['notes']
        if 'last_page_seen' in data:
            try:
                paper.last_page_seen = int(data['last_page_seen']) if data['last_page_seen'] is not None else None
            except ValueError:
                return jsonify({'error': 'Invalid page number'}), 400
        
        db.session.commit()
        return jsonify(paper.to_dict())

@app.route('/api/paper/<int:paper_id>/delete', methods=['DELETE', 'POST'])
@firebase_auth_required
def api_delete_paper(paper_id):
    """Deletes a specific paper via API."""
    paper = Paper.query.get_or_404(paper_id)
    repo_id = paper.repository_id
    
    try:
        full_filepath = os.path.join(app.config['UPLOAD_FOLDER'], paper.filepath)
        if os.path.exists(full_filepath):
            os.remove(full_filepath)
        
        db.session.delete(paper)
        db.session.commit()
        return jsonify({'message': f'Paper "{paper.title}" deleted successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/uploads/<path:filepath>', methods=['GET', 'POST', 'PUT'])
def api_serve_paper(filepath):
    """Serves the uploaded paper files via API."""
    repo_subfolder = os.path.dirname(filepath)  
    filename = os.path.basename(filepath) 
    directory = os.path.join(app.config['UPLOAD_FOLDER'], repo_subfolder)
    return send_from_directory(directory, filename, as_attachment=False)


@app.route('/api/books')
def search_books():
    """Proxy for book search"""
    query = request.args.get('q', '')
    if not query:
        return jsonify({'error': 'Query parameter "q" is required'}), 400
    
    try:
        response = requests.get(
            f'{BIBIFY_API_BASE}/api/books',
            params={'q': query},
            timeout=30
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'API request failed: {str(e)}'}), 500

@app.route('/api/website')
def get_website_info():
    """Proxy for website metadata"""
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400
    
    try:
        response = requests.get(
            f'{BIBIFY_API_BASE}/api/website',
            params={'url': url},
            timeout=30
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'API request failed: {str(e)}'}), 500

@app.route('/api/cite', methods=['GET', 'POST'])
def generate_citation():
    """Proxy for citation generation"""
    try:
        params = dict(request.args)
        logger.debug(f"Sending request to Bibify API with params: {params}")
        response = requests.get(
            f'{BIBIFY_API_BASE}/api/cite',
            params=params,
            timeout=30
        )
        logger.debug(f"Bibify API response status: {response.status_code}")
        logger.debug(f"Bibify API response content: {response.text}")
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        logger.error(f"Citation generation failed: {str(e)}", exc_info=True)
        return jsonify({'error': f'Citation generation failed: {str(e)}'}), 500
    except ValueError as e:
        logger.error(f"Failed to parse Bibify API response: {str(e)}", exc_info=True)
        return jsonify({'error': f'Invalid response from citation API: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Unexpected error in citation generation: {str(e)}", exc_info=True)
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Citation generation failed: {str(e)}'}), 500

@app.route('/api/styles')
def get_citation_styles():
    """Proxy for citation styles"""
    limit = request.args.get('limit', '20')
    
    try:
        response = requests.get(
            f'{BIBIFY_API_BASE}/api/styles',
            params={'limit': limit},
            timeout=30
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to load styles: {str(e)}'}), 500

@app.route('/api/styles/search')
def search_citation_styles():
    """Proxy for citation style search"""
    query = request.args.get('q', '')
    if not query:
        return jsonify({'error': 'Query parameter "q" is required'}), 400
    
    try:
        response = requests.get(
            f'{BIBIFY_API_BASE}/api/styles/search',
            params={'q': query},
            timeout=30
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Style search failed: {str(e)}'}), 500

@app.route('/api/fields/<media_type>')
def get_citation_fields(media_type):
    """Proxy for citation fields by media type"""
    try:
        response = requests.get(
            f'{BIBIFY_API_BASE}/api/fields/{media_type}',
            timeout=30
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to get fields: {str(e)}'}), 500

# Health check endpoint
@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'service': 'bibify-proxy'})

# Add this to app.py
@app.route('/api/repository/<int:repo_id>', methods=['DELETE'])
@firebase_auth_required
def delete_repository(repo_id):
    repo = Repository.query.get_or_404(repo_id)
    if repo.user_id != g.user_id:
        return jsonify({'error': 'Unauthorized access to repository'}), 403
    db.session.delete(repo)
    db.session.commit()
    return jsonify({'message': f'Repository "{repo.name}" deleted'}), 200

if __name__ == '__main__':
    app.run(debug=True)






































































