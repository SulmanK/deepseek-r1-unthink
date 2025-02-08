from flask import Flask, request, Response
from flask_cors import CORS
import requests
import json
import re

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": "*"}})

# Configuration
OLLAMA_SERVER = "http://localhost:11434" # Default OLLAMA_HOST 11434
PROXY_PORT = 11435 # forwarded new ENDPOINT

def process_thinking_content(message_content, thinking_started):
    """Process content based on thinking tags state"""
    if not message_content:
        return "", thinking_started

    # First clean up markdown code blocks
    if '```' in message_content:
        # Extract content between ```json and ``` if it exists
        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', message_content, re.DOTALL)
        if json_match:
            message_content = json_match.group(1).strip()

    # Handle opening tag
    if '<think>' in message_content:
        thinking_started = True
        # If there's content before <think>, keep it
        content_before = message_content.split('<think>')[0]
        return content_before, thinking_started

    # Handle closing tag
    if '</think>' in message_content:
        thinking_started = False
        # If there's content after </think>, keep it
        content_after = message_content.split('</think>')[-1]
        return content_after, thinking_started

    # If we're in thinking mode, return empty
    if thinking_started:
        return "", thinking_started

    return message_content, thinking_started

def is_empty_content(message_content):
    """Check if the content is empty or just whitespace/newlines"""
    if not message_content:
        return True
    return not message_content.strip()

def clean_response_content(content):
    """Clean thinking tags from response content"""
    if not content:
        return content
        
    # Remove everything between <think> and </think> tags
    while '<think>' in content and '</think>' in content:
        start = content.find('<think>')
        end = content.find('</think>') + len('</think>')
        content = content[:start] + content[end:]
    
    # Clean up excessive newlines
    content = '\n'.join(filter(None, content.split('\n')))
    return content.strip()

@app.route('/api/<path:path>', methods=['POST'])
def proxy_api(path):
    if path not in ['generate', 'chat']:
        return Response('Not Found', status=404)

    # Get request data
    request_data = request.json
    is_streaming = request_data.get('stream', True)

    # Make request to Ollama
    response = requests.post(
        f"{OLLAMA_SERVER}/api/{path}",
        json=request_data,
        stream=is_streaming
    )

    # Handle non-streaming response
    if not is_streaming:
        try:
            data = response.json()
            if 'response' in data:
                data['response'] = clean_response_content(data['response'])
            return Response(
                json.dumps(data),
                mimetype='application/json'
            )
        except Exception as e:
            print(f"Error processing non-streaming response: {e}")
            return Response(response.content, response.status_code)

    # Handle streaming response
    def generate():
        thinking_started = False
        for chunk in response.iter_lines():
            if chunk:
                try:
                    # Parse the JSON response
                    data = json.loads(chunk.decode('utf-8'))
                    
                    # Check if this is a message with content
                    if 'message' in data and 'content' in data['message']:
                        content = data['message']['content']
                        
                        # Process thinking tags and get cleaned content
                        cleaned_content, thinking_started = process_thinking_content(
                            content, thinking_started
                        )
                        
                        # Skip if content is empty after processing
                        if is_empty_content(cleaned_content):
                            continue
                            
                        # Update the content in the data
                        data['message']['content'] = cleaned_content
                        
                        # Convert back to JSON and send
                        yield json.dumps(data).encode('utf-8') + b'\n'
                    else:
                        # Forward non-content messages (like 'done' messages)
                        yield chunk + b'\n'
                        
                except json.JSONDecodeError:
                    # If we can't parse the JSON, forward it anyway
                    yield chunk + b'\n'

    return Response(
        generate(),
        mimetype='application/json',
        headers={
            'X-Accel-Buffering': 'no',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json'
        }
    )

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>', methods=['GET', 'OPTIONS'])
def catch_all(path):
    if request.method == 'OPTIONS':
        return Response('', 204)
    
    resp = requests.request(
        method=request.method,
        url=f"{OLLAMA_SERVER}/{path}",
        headers={key: value for key, value in request.headers if key != 'Host'},
        data=request.get_data(),
        cookies=request.cookies,
        allow_redirects=False
    )
    
    excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
    headers = [(name, value) for (name, value) in resp.raw.headers.items()
               if name.lower() not in excluded_headers]
    
    return Response(resp.content, resp.status_code, headers)

if __name__ == '__main__':
    print(f"Starting proxy server on port {PROXY_PORT}")
    print(f"Forwarding requests to {OLLAMA_SERVER}")
    app.run(port=PROXY_PORT, debug=True)
