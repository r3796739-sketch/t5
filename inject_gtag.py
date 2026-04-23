import os
import glob

gtag_snippet = """
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-M6N6YSPRHC"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());

      gtag('config', 'G-M6N6YSPRHC');
    </script>"""

html_files = glob.glob('static_site/**/*.html', recursive=True)
count = 0

for file in html_files:
    try:
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        if 'G-M6N6YSPRHC' not in content and '<head>' in content:
            new_content = content.replace('<head>', f'<head>\n{gtag_snippet}', 1)
            with open(file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            count += 1
            print(f"Added gtag to {file}")
            
    except Exception as e:
        print(f"Failed on {file}: {e}")

print(f"\nSuccess! Injected Google Analytics into {count} static HTML files.")
