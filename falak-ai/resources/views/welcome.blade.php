<!DOCTYPE html>
<html lang="uz">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Falak AI</title>
        <style>
            *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

            html, body {
                height: 100%;
            }

            body {
                font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background-image: url('/bg.jpg');
                background-size: cover;
                background-position: center;
                background-repeat: no-repeat;
            }

            .overlay {
                position: fixed;
                inset: 0;
                background: linear-gradient(
                    to bottom,
                    rgba(5, 15, 35, 0.60) 0%,
                    rgba(5, 15, 35, 0.75) 100%
                );
            }

            .content {
                position: relative;
                z-index: 1;
                text-align: center;
                padding: 2rem;
                max-width: 720px;
                width: 100%;
            }

            .logo {
                width: 150px;
                height: 150px;
                object-fit: contain;
                margin: 0 auto 1.5rem;
                filter: drop-shadow(0 4px 24px rgba(0,0,0,0.5));
            }

            h1 {
                font-size: clamp(2.5rem, 6vw, 4rem);
                font-weight: 700;
                color: #ffffff;
                letter-spacing: 0.04em;
                text-shadow: 0 2px 16px rgba(0,0,0,0.5);
                margin-bottom: 1rem;
            }

            h1 span {
                color: #4fc3f7;
            }

            p.subtitle {
                font-size: clamp(0.95rem, 2.2vw, 1.2rem);
                color: rgba(220, 235, 255, 0.88);
                line-height: 1.65;
                text-shadow: 0 1px 6px rgba(0,0,0,0.4);
                max-width: 600px;
                margin: 0 auto;
            }
        </style>
    </head>
    <body>
        <div class="overlay"></div>

        <main class="content">
            <img src="/icon_transparent.png" alt="Falak AI logo" class="logo">

            <h1>Falak <span>AI</span></h1>

            <p class="subtitle">
                Urbanistika va infratuzilmani sun'iy yo'ldosh ma'lumotlari asosida aqlli monitoring qilish tizimi
            </p>
        </main>
    </body>
</html>
