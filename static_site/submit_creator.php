<?php
/**
 * YoppyChat Creator Submission Handler
 * 
 * This script handles the creator channel submission form.
 * It validates inputs, saves to a JSON file, and sends email notifications.
 * 
 * SETUP INSTRUCTIONS:
 * 1. Upload this file to your shared hosting
 * 2. Update the $ADMIN_EMAIL variable with your email address
 * 3. Set $SEND_EMAIL = true to receive email notifications
 * 4. Make sure the 'data' directory exists and is writable (chmod 755)
 */

header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST');
header('Access-Control-Allow-Headers: Content-Type');

// Handle preflight requests
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit();
}

// Only accept POST requests
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['success' => false, 'message' => 'Method not allowed']);
    exit();
}

// ============================================
// CONFIGURATION - UPDATE THESE VALUES
// ============================================
$DATA_FILE = __DIR__ . '/data/creator_submissions.json';
$ADMIN_EMAIL = 'hello@yoppychat.com'; // <-- Change this to your email
$SEND_EMAIL = false; // <-- Set to true to enable email notifications
// ============================================

// Get and sanitize input
$email = filter_input(INPUT_POST, 'email', FILTER_SANITIZE_EMAIL);
$channelLink = filter_input(INPUT_POST, 'channel_link', FILTER_SANITIZE_URL);

// Validation functions
function validateEmail($email)
{
    return filter_var($email, FILTER_VALIDATE_EMAIL) !== false;
}

function validateChannelLink($url)
{
    $patterns = [
        '/^https?:\/\/(www\.)?youtube\.com\/@[\w-]+/i',
        '/^https?:\/\/(www\.)?youtube\.com\/c\/[\w-]+/i',
        '/^https?:\/\/(www\.)?youtube\.com\/channel\/[\w-]+/i',
        '/^https?:\/\/(www\.)?youtube\.com\/user\/[\w-]+/i'
    ];

    foreach ($patterns as $pattern) {
        if (preg_match($pattern, $url)) {
            return true;
        }
    }
    return false;
}

// Validate inputs
$errors = [];

if (empty($email)) {
    $errors[] = 'Email is required';
} elseif (!validateEmail($email)) {
    $errors[] = 'Invalid email address';
}

if (empty($channelLink)) {
    $errors[] = 'Channel link is required';
} elseif (!validateChannelLink($channelLink)) {
    $errors[] = 'Invalid YouTube channel link';
}

if (!empty($errors)) {
    http_response_code(400);
    echo json_encode([
        'success' => false,
        'message' => implode(', ', $errors),
        'errors' => $errors
    ]);
    exit();
}

// Prepare submission data
$submission = [
    'id' => uniqid('creator_', true),
    'email' => $email,
    'channel_link' => $channelLink,
    'submitted_at' => date('Y-m-d H:i:s'),
    'ip_address' => $_SERVER['REMOTE_ADDR'] ?? 'unknown',
    'user_agent' => $_SERVER['HTTP_USER_AGENT'] ?? 'unknown',
    'status' => 'pending'
];

// Load existing submissions
$submissions = [];
if (file_exists($DATA_FILE)) {
    $content = file_get_contents($DATA_FILE);
    if ($content) {
        $submissions = json_decode($content, true) ?? [];
    }
}

// Check for duplicate submissions (same email within last 24 hours)
$now = time();
foreach ($submissions as $existing) {
    if ($existing['email'] === $email) {
        $submittedAt = strtotime($existing['submitted_at']);
        if (($now - $submittedAt) < 86400) { // 24 hours
            http_response_code(429);
            echo json_encode([
                'success' => false,
                'message' => 'You have already submitted a request. Please wait 24 hours before submitting again.'
            ]);
            exit();
        }
    }
}

// Add new submission
$submissions[] = $submission;

// Ensure data directory exists
$dataDir = dirname($DATA_FILE);
if (!is_dir($dataDir)) {
    mkdir($dataDir, 0755, true);
}

// Save submissions
$saved = file_put_contents($DATA_FILE, json_encode($submissions, JSON_PRETTY_PRINT));

if ($saved === false) {
    http_response_code(500);
    echo json_encode([
        'success' => false,
        'message' => 'Failed to save submission. Please try again.'
    ]);
    exit();
}

// Send email notification (optional)
if ($SEND_EMAIL && !empty($ADMIN_EMAIL)) {
    $subject = "🎬 New Creator Submission - YoppyChat";
    $message = "
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
            .container { max-width: 600px; margin: 0 auto; padding: 20px; }
            .header { background: linear-gradient(135deg, #ff9a56, #ff8c42); color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }
            .content { background: #f8f8f8; padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px; }
            .field { margin-bottom: 15px; padding: 10px; background: white; border-radius: 6px; }
            .label { font-weight: bold; color: #666; font-size: 12px; text-transform: uppercase; }
            .value { color: #333; font-size: 16px; margin-top: 5px; }
            a { color: #ff9a56; text-decoration: none; }
            a:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <div class='container'>
            <div class='header'>
                <h2 style='margin:0;'>🎬 New Creator Submission</h2>
                <p style='margin:10px 0 0 0; opacity: 0.9;'>A YouTuber wants to join YoppyChat!</p>
            </div>
            <div class='content'>
                <div class='field'>
                    <div class='label'>Email Address</div>
                    <div class='value'><a href='mailto:{$email}'>{$email}</a></div>
                </div>
                <div class='field'>
                    <div class='label'>YouTube Channel</div>
                    <div class='value'><a href='{$channelLink}' target='_blank'>{$channelLink}</a></div>
                </div>
                <div class='field'>
                    <div class='label'>Submitted At</div>
                    <div class='value'>{$submission['submitted_at']}</div>
                </div>
                <div class='field'>
                    <div class='label'>IP Address</div>
                    <div class='value'>{$submission['ip_address']}</div>
                </div>
                <div class='field'>
                    <div class='label'>Submission ID</div>
                    <div class='value' style='font-family: monospace; font-size: 12px;'>{$submission['id']}</div>
                </div>
            </div>
        </div>
    </body>
    </html>
    ";

    $headers = [
        'MIME-Version: 1.0',
        'Content-type: text/html; charset=UTF-8',
        'From: YoppyChat <hello@yoppychat.com>',
        'Reply-To: ' . $email
    ];

    @mail($ADMIN_EMAIL, $subject, $message, implode("\r\n", $headers));
}

// Return success response
http_response_code(200);
echo json_encode([
    'success' => true,
    'message' => 'Your submission has been received! We will review your channel and contact you within 24 hours.',
    'submission_id' => $submission['id']
]);
?>