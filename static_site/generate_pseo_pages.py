import os
import urllib.parse

# Define the data sources and deployment destinations
sources = {
    "youtube": {
        "name": "YouTube Channel",
        "icon_svg": '<svg class="w-8 h-8 text-[#FF0000]" viewBox="0 0 24 24" fill="currentColor"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>',
        "color": "#FF0000",
        "description": "your video transcripts",
        "action": "extract spoken knowledge from your videos",
        "benefit": "without having to manually transcribe or organize your content"
    },
    "website": {
        "name": "Website URL",
        "icon_svg": '<svg class="w-8 h-8 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" /></svg>',
        "color": "#ff9a56",
        "description": "your website URLs",
        "action": "scrape and vectorize your public web pages, FAQs, and help centers",
        "benefit": "ensuring your agent always has the most up-to-date information"
    },
    "whatsapp-export": {
        "name": "WhatsApp Chat Export",
        "icon_svg": '<svg class="w-8 h-8 text-[#25D366]" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51a12.8 12.8 0 0 0-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 0 0-3.48-8.413z"/></svg>',
        "color": "#25D366",
        "description": "your WhatsApp chat exports",
        "action": "ingest your historical .txt chat logs to learn your exact brand tone and sales framing",
        "benefit": "replicating the success of your best human sales representatives"
    },
    "pdf-documents": {
        "name": "PDF Documents",
        "icon_svg": '<svg class="w-8 h-8 text-[#F40F02]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>',
        "color": "#F40F02",
        "description": "your PDFs and internal documents",
        "action": "parse complex technical manuals, product sheets, and onboarding guides",
        "benefit": "to instantly locate specific paragraphs in hundreds of pages across milliseconds"
    }
}

destinations = {
    "discord": {
        "name": "Discord",
        "icon_svg": '<svg class="w-8 h-8 text-[#5865F2]" fill="currentColor" viewBox="0 0 24 24"><path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"/></svg>',
        "color": "#5865F2",
        "audience": "Discord community members",
        "urgency": "answer technical questions and moderate sentiment in real-time"
    },
    "telegram": {
        "name": "Telegram",
        "icon_svg": '<svg class="w-8 h-8 text-[#2AABEE]" fill="currentColor" viewBox="0 0 24 24"><path d="M12 24c6.627 0 12-5.373 12-12S18.627 0 12 0 0 5.373 0 12s5.373 12 12 12zm5.894-17.5a.782.782 0 011.094.258.824.824 0 01.118.528l-1.558 11.231c-.087.653-.615 1.119-1.274 1.119-.188 0-.374-.045-.544-.132l-4.706-2.617-1.928 2.052a.807.807 0 01-1.385-.54v-3.791l7.351-6.72c.188-.172.115-.465-.138-.521-.252-.055-.494.07-.611.29l-8.683 8.324-4.561-1.6c-.632-.222-1.04-.816-1.028-1.493a1.595 1.595 0 011.002-1.383l14.85-5.998z"/></svg>',
        "color": "#2AABEE",
        "audience": "Telegram group members",
        "urgency": "provide instant value and announcements in your chat"
    },
    "whatsapp": {
        "name": "WhatsApp Business API",
        "icon_svg": '<svg class="w-8 h-8 text-[#25D366]" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51a12.8 12.8 0 0 0-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 0 0-3.48-8.413z"/></svg>',
        "color": "#25D366",
        "audience": "WhatsApp leads and clients",
        "urgency": "convert inbound sales queries and handle customer support 24/7"
    },
    "website-widget": {
        "name": "Website Widget Embed",
        "icon_svg": '<svg class="w-8 h-8 text-[#ff9a56]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>',
        "color": "#ff9a56",
        "audience": "website visitors",
        "urgency": "qualify traffic and guide users to the right resources directly on your homepage"
    }
}

# Template for the HTML page
html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <link rel="canonical" href="https://yoppychat.com/integrations/{source_slug}-to-{dest_slug}.html" />
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="Learn how to effortlessly connect your {source_name} to a {dest_name} AI chatbot. Deploy a custom generative RAG agent using your exact data in seconds.">
    <title>Connect {source_name} to a {dest_name} AI Chatbot | YoppyChat Integrations</title>
    <link rel="icon" type="image/png" href="../images/logo/favicon.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    colors: {{
                        primary: '#ff9a56',
                        secondary: '#ff8c42',
                        'text-primary': '#2a1f16',
                        'text-secondary': '#5a4a32',
                        'text-muted': '#7d6847',
                        'neutral-100': '#f8f1e8',
                        'neutral-200': '#f0e6d6',
                        cream: '#fdf8f3',
                    }},
                    fontFamily: {{
                        sans: ['Inter', 'sans-serif'],
                    }},
                }}
            }}
        }}
    </script>
    <style>
        body {{ font-family: 'Inter', sans-serif; background: #fdf8f3; color: #2a1f16; }}
        .gradient-text {{ background: linear-gradient(135deg, {source_color} 0%, {dest_color} 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }}
    </style>
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "SoftwareApplication",
      "name": "YoppyChat {source_name} to {dest_name} Integration",
      "applicationCategory": "BusinessApplication",
      "operatingSystem": "Web",
      "description": "Connect {source_name} data to a {dest_name} AI chatbot instantly."
    }}
    </script>
</head>
<body class="min-h-screen">
    <!-- Header -->
    <header class="fixed top-0 left-0 right-0 z-50 bg-white/80 backdrop-blur-xl border-b border-neutral-200">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <nav class="flex items-center justify-between h-16 lg:h-20">
                <a href="https://yoppychat.com/" class="flex items-center gap-2 group">
                    <img src="../images/logo/primiry__logo.png" alt="YoppyChat Logo" class="h-12 lg:h-14 w-auto">
                </a>
                <div class="flex items-center gap-4">
                    <a href="https://app.yoppychat.com/channel" class="px-6 py-2.5 rounded-full bg-gradient-to-r from-primary to-secondary text-white font-semibold shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all duration-300">
                        Try Free
                    </a>
                </div>
            </nav>
        </div>
    </header>

    <main class="relative z-10 pt-32 pb-16">
        <div class="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
            
            <div class="text-center mb-12">
                <div class="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary/10 border border-primary/20 mb-6">
                    <span class="text-sm font-medium text-primary">Integration Guide</span>
                </div>
                <h1 class="text-4xl lg:text-5xl font-bold text-text-primary mb-6 leading-tight">
                    Connect <span style="color: {source_color}">{source_name}</span> to a <span style="color: {dest_color}">{dest_name}</span> AI Chatbot
                </h1>
                <p class="text-xl text-text-secondary max-w-2xl mx-auto">Deploy a custom Generative AI agent that reads {source_desc} and instantly replies to your {dest_audience}.</p>
            </div>

            <!-- The Architecture Diagram -->
            <div class="flex items-center justify-center gap-4 lg:gap-8 mb-16 bg-white p-8 rounded-3xl border border-neutral-200 shadow-sm">
                <div class="flex flex-col items-center">
                    <div class="w-20 h-20 rounded-2xl bg-neutral-100 flex items-center justify-center border border-neutral-200 shadow-sm">
                        {source_svg}
                    </div>
                    <p class="mt-3 font-semibold text-sm text-center">{source_name}</p>
                </div>

                <!-- Animated Arrow -->
                <div class="hidden sm:flex flex-col items-center text-primary">
                    <svg class="w-8 h-8 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
                    <span class="text-xs font-bold uppercase tracking-wider mt-2 opacity-75">Connects via</span>
                </div>

                <div class="flex flex-col items-center mx-2 lg:mx-4">
                    <div class="w-24 h-24 rounded-full bg-white flex items-center justify-center border-4 border-primary shadow-lg relative">
                        <img src="../images/logo/favicon.png" class="w-12 h-12" alt="YoppyChat Engine">
                    </div>
                </div>

                <!-- Animated Arrow -->
                <div class="hidden sm:flex flex-col items-center text-primary">
                    <svg class="w-8 h-8 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
                    <span class="text-xs font-bold uppercase tracking-wider mt-2 opacity-75">Routes to</span>
                </div>

                <div class="flex flex-col items-center">
                    <div class="w-20 h-20 rounded-2xl bg-neutral-100 flex items-center justify-center border border-neutral-200 shadow-sm">
                        {dest_svg}
                    </div>
                    <p class="mt-3 font-semibold text-sm text-center">{dest_name}</p>
                </div>
            </div>

            <div class="bg-white rounded-3xl shadow-xl border border-neutral-200 p-8 lg:p-12 mb-12">
                <h2 class="text-2xl font-bold mb-4">How the integration works</h2>
                <p class="text-text-secondary leading-relaxed mb-6">
                    YoppyChat's Retrieval-Augmented Generation (RAG) architecture acts as a seamless bridge between your content and your community. First, our platform will securely connect to {source_name} to {source_action}. This creates a dedicated "Brain" for your agent {source_benefit}.
                </p>
                <p class="text-text-secondary leading-relaxed mb-6">
                    Next, you will generate a one-click deployment for {dest_name}. Whenever a user asks a question, the agent searches your {source_name} database, retrieves the exact factual answer, and formats it naturally for {dest_name} to {dest_urgency}. No hallucinations, no manual flowcharts, just automated knowledge transfer.
                </p>

                <h3 class="text-xl font-bold mt-10 mb-4">Step-by-Step Setup</h3>
                <ol class="list-decimal pl-5 space-y-3 text-text-secondary">
                    <li><strong>Create a free YoppyChat account</strong> and create a new Persona workspace.</li>
                    <li><strong>Ingest your data:</strong> In the Knowledge Base section, select <strong>{source_name}</strong> and let the AI index your content.</li>
                    <li><strong>Set your bot's personality:</strong> Define the tone and rules for your new digital agent.</li>
                    <li><strong>Deploy:</strong> Go to the Integrations tab, authorize the connection for <strong>{dest_name}</strong>, and watch your bot answer its first question autonomously.</li>
                </ol>
            </div>

            <div class="text-center">
                <a href="https://app.yoppychat.com/channel" class="inline-block px-8 py-4 rounded-full font-bold bg-gradient-to-r from-primary to-secondary text-white shadow-xl hover:-translate-y-1 hover:shadow-primary/30 transition-all duration-300">
                    Connect {source_name} to {dest_name} Now
                </a>
            </div>

        </div>
    </main>
</body>
</html>
"""

# Create the integrations directory if it doesn't exist
output_dir = "integrations"
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# Generate the programmatic pages
count = 0
for source_slug, source_data in sources.items():
    for dest_slug, dest_data in destinations.items():
        # Generate final HTML
        html_content = html_template.format(
            source_slug=source_slug,
            dest_slug=dest_slug,
            source_name=source_data["name"],
            source_color=source_data["color"],
            source_svg=source_data["icon_svg"],
            source_desc=source_data["description"],
            source_action=source_data["action"],
            source_benefit=source_data["benefit"],
            dest_name=dest_data["name"],
            dest_color=dest_data["color"],
            dest_svg=dest_data["icon_svg"],
            dest_audience=dest_data["audience"],
            dest_urgency=dest_data["urgency"]
        )
        
        # Write to file
        filename = f"{source_slug}-to-{dest_slug}.html"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        count += 1

print(f"Successfully generated {count} programmatic SEO pages in the '{output_dir}/' directory.")
