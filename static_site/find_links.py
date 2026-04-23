import os, re

html_files = []
for root, _, files in os.walk(r"c:\Users\Nikhil\Downloads\_public_html"):
    for f in files:
        if f.endswith('.html'):
            html_files.append(os.path.join(root, f))

base_dir = r"c:\Users\Nikhil\Downloads\_public_html"
broken_links = []

for html_file in html_files:
    with open(html_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Simple regex to find hrefs
    matches = re.findall(r'href=[\"\']([^\"\']+)[\"\']', content)
    for match in matches:
        if match.startswith('http') or match.startswith('mailto:') or match.startswith('#') or match.startswith('{{'):
            # Ignore absolute external links, mailto, anchor links, and template tags
            continue
            
        # Ignore empty or just '/'
        if not match or match == '/':
            continue

        # Convert URL path to local path approximation
        local_path = match
        if local_path.startswith('/'):
            local_path = local_path[1:]
        
        # Remove query params or hashes
        local_path = local_path.split('?')[0].split('#')[0]

        if not local_path:
            continue
            
        full_local_path = os.path.join(base_dir, local_path)
        full_local_path = os.path.normpath(full_local_path)
        
        if not os.path.exists(full_local_path):
            broken_links.append((html_file, match, full_local_path))

if broken_links:
    print("Found broken links:")
    for file, link, path in broken_links:
        print(f"File: {os.path.basename(file)} -> Link: {link} (Expected at: {path})")
else:
    print("No broken links found among local references!")
