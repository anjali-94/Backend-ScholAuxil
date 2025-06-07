from functools import wraps
from flask import request, jsonify, g
from firebase_admin import auth
from firebase_admin.auth import InvalidIdTokenError, ExpiredIdTokenError, RevokedIdTokenError
import logging

logging.basicConfig(level=logging.DEBUG)

def firebase_auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization', None)
        logging.info(f"Received Authorization header: {auth_header}")
        if not auth_header:
            return jsonify({"message": "Missing Authorization Header"}), 401
        
        parts = auth_header.split()
        if parts[0].lower() != 'bearer' or len(parts) != 2:
            return jsonify({"message": "Invalid Authorization Header"}), 401
        
        id_token = parts[1]
        try:
            decoded_token = auth.verify_id_token(id_token)
            g.user_id = decoded_token['uid']
            logging.info(f"Authenticated user: {g.user_id}")
        except InvalidIdTokenError:
            logging.error("Invalid Firebase ID token")
            return jsonify({"message": "Invalid token", "error": "Token is invalid"}), 401
        except ExpiredIdTokenError:
            logging.error("Expired Firebase ID token")
            return jsonify({"message": "Invalid token", "error": "Token has expired"}), 401
        except RevokedIdTokenError:
            logging.error("Revoked Firebase ID token")
            return jsonify({"message": "Invalid token", "error": "Token has been revoked"}), 401
        except Exception as e:
            logging.error(f"Authentication error: {str(e)}")
            return jsonify({"message": "Authentication failed", "error": str(e)}), 401
        
        return f(*args, **kwargs)
    return decorated_function