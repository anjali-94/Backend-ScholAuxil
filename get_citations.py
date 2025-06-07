# get_citations.py with improved feedback
import requests
import re
import json
from citation_api import extract_citation_candidates, get_citation_by_metadata

def get_citations(text):
    """
    Process text and return citations using the citation API
    
    Args:
        text: Text extracted from PDF
        
    Returns:
        List of citation dictionaries
    """
    try:
        # First, extract candidate citations from the text
        candidates = extract_citation_candidates(text)
        
        if not candidates:
            print("No citation candidates found in text")
            return []
        
        print(f"Found {len(candidates)} citation candidates")
        
        # Get full citation information for each candidate
        citations = []
        for candidate in candidates:
            try:
                citation = get_citation_by_metadata(
                    title=candidate.get("title", ""),
                    author=candidate.get("author", ""),
                    year=candidate.get("year", "")
                )
                
                # Add original context
                citation["context"] = candidate.get("context", "")
                citations.append(citation)
                
                # Rate limiting to avoid API throttling
                import time
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error getting citation for {candidate}: {e}")
                # Still include the candidate even if API lookup failed
                citations.append({
                    "author": candidate.get("author", ""),
                    "year": candidate.get("year", ""),
                    "title": candidate.get("title", ""),
                    "context": candidate.get("context", ""),
                    "error": str(e),
                    "apa": f"{candidate.get('author', 'Unknown')} ({candidate.get('year', 'n.d.')}). {candidate.get('title', 'Untitled')}.",
                    "mla": f"{candidate.get('author', 'Unknown')}. \"{candidate.get('title', 'Untitled')}.\" {candidate.get('year', 'n.d.')}.",
                    "chicago": f"{candidate.get('author', 'Unknown')}. \"{candidate.get('title', 'Untitled')}.\" {candidate.get('year', 'n.d.')}.",
                    "bibtex": f"@misc{{{candidate.get('author', 'unknown')}{candidate.get('year', '')},\n  author = {{{candidate.get('author', 'Unknown')}}},\n  title = {{{candidate.get('title', 'Untitled')}}},\n  year = {{{candidate.get('year', 'n.d.')}}}\n}}"
                })
        
        return citations
    except Exception as e:
        print(f"Citation API error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

def send_pdf_to_api(pdf_path):
    """
    Send a PDF directly to a citation API (alternative method)
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Citation information returned by the API
    """
    try:
        # First try to use the local processing pipeline
        from preprocess_data import extract_text_with_citations
        
        # Extract text from PDF with improved citation preservation
        extracted_text = extract_text_with_citations(pdf_path)
        
        # Save extracted text for debugging
        debug_text_path = f"{pdf_path}_extracted.txt"
        with open(debug_text_path, 'w', encoding='utf-8') as f:
            f.write(extracted_text)
        
        # Get citations from the processed text
        results = get_citations(extracted_text)
        
        # Save citations for debugging
        debug_citations_path = f"{pdf_path}_citations.json"
        with open(debug_citations_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
            
        return results
    except Exception as e:
        print(f"Local processing error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}