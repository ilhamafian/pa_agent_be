# Lofy - WhatsApp Personal Assistant Backend

A powerful AI-powered WhatsApp assistant that helps users manage their calendar, tasks, reminders, and notes through natural language conversations. Built with FastAPI, MongoDB, and OpenAI's GPT-4.

## 🚀 Features

### 📅 Calendar Management

- **Natural Language Scheduling**: Create events using conversational language
- **Google Calendar Integration**: Seamless OAuth2 integration with Google Calendar
- **Smart Event Detection**: Automatically detects and schedules bookings from templates (perfect for freelancers and service providers)
- **Event Updates**: Modify existing events with simple commands
- **Flexible Time Ranges**: View events for "today", "tomorrow", "next week", or custom date ranges

### ⏰ Reminder System

- **Event Reminders**: Set reminders before existing calendar events
- **Custom Reminders**: Create standalone reminders for any task
- **Natural Time Expressions**: Accept time inputs like "in 3 hours", "tomorrow at 9am", "30 minutes from now"
- **WhatsApp Notifications**: Receive reminders directly in WhatsApp

### ✅ Task Management

- **Priority-Based Tasks**: High 🔴, Medium 🟡, and Low 🟢 priority levels
- **Smart Priority Detection**: Automatically infers priority from task content
- **Status Tracking**: Pending, In Progress, and Completed states
- **Task Organization**: View tasks by status and priority

### 📝 Notes & Knowledge Management

- **Semantic Search**: Find notes using natural language queries
- **AI-Powered Titles**: Auto-generate meaningful titles for notes
- **Vector Embeddings**: Uses OpenAI embeddings for intelligent note retrieval
- **Smart Confirmation**: Asks before saving valuable content as notes

### 🔐 Security & Privacy

- **Phone Number Encryption**: Secure storage of user phone numbers
- **PIN-Based Authentication**: Simple yet secure user authentication
- **OAuth2 Integration**: Secure Google Calendar access
- **Data Hashing**: All sensitive data is properly hashed

## 🛠️ Tech Stack

- **Backend**: FastAPI (Python)
- **Database**: MongoDB with Atlas Vector Search
- **AI**: OpenAI GPT-4 and Embeddings
- **Authentication**: JWT tokens, OAuth2
- **Scheduling**: APScheduler
- **Messaging**: WhatsApp Business API
- **Calendar**: Google Calendar API

## 📁 Project Structure

```
pa_agent_be/
├── main.py                 # FastAPI application entry point
├── llm.py                  # OpenAI integration and conversation handling
├── user.py                 # User authentication and profile management
├── integrations.py         # External service integrations
├── dashboard.py           # Dashboard API endpoints
├── settings.py            # User settings management
├── db/
│   ├── mongo.py          # MongoDB connection and utilities
│   └── __init__.py
├── tools/
│   ├── calendar.py       # Google Calendar operations
│   ├── reminder.py       # Reminder management
│   ├── task.py          # Task management
│   ├── notes.py         # Notes and knowledge management
│   └── scheduler.py     # Background job scheduling
├── utils/
│   └── utils.py         # Utility functions
├── test/                # Test files
├── requirements.txt     # Python dependencies
└── system_prompt.txt    # AI assistant system prompt
```

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- MongoDB Atlas account
- OpenAI API key
- WhatsApp Business API access
- Google Cloud Console project with Calendar API enabled

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/yourusername/pa_agent_be.git
   cd pa_agent_be
   ```

2. **Create virtual environment**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Setup** Create a `.env` file with the following variables:

   ```env
   # Database
   MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/

   # OpenAI
   OPENAI_API_KEY=your_openai_api_key

   # WhatsApp
   VERIFY_TOKEN=your_whatsapp_verify_token

   # Google OAuth
   GOOGLE_CLIENT_ID=your_google_client_id
   GOOGLE_CLIENT_SECRET=your_google_client_secret
   SCOPES=["https://www.googleapis.com/auth/calendar"]

   # Application
   APP_URL=https://your-app-url.com
   FRONTEND_URL=https://your-frontend-url.com
   TOKEN_SECRET_KEY=your_jwt_secret_key
   ```

5. **Google OAuth Setup**

   - Download your OAuth2 credentials and save as `credentials.json`
   - Ensure the file is in the project root directory

6. **Run the application**
   ```bash
   python main.py
   ```

The server will start on `http://localhost:8000`

## 📚 API Documentation

### Core Endpoints

- `GET /` - Health check
- `POST /auth/callback` - WhatsApp webhook handler
- `GET /auth/google_callback` - Google OAuth callback

### User Management

- `POST /user_onboarding` - Create new user account
- `POST /login` - User authentication
- `POST /logout` - User logout
- `POST /check_phone_number_exist` - Check if phone number exists

### Settings & Integrations

- `GET /get_settings_info` - Get user settings
- `POST /update_profile` - Update user profile
- `POST /update_notifications` - Update notification preferences
- `GET /get_integrations` - Get integration status
- `GET /google_auth_url` - Get Google OAuth URL

## 🤖 How It Works

1. **User Onboarding**: New users receive a WhatsApp message with onboarding link
2. **Authentication**: Users create accounts with phone number and PIN
3. **Calendar Integration**: Users authorize Google Calendar access via OAuth2
4. **Natural Language Processing**: GPT-4 processes user messages and determines actions
5. **Tool Execution**: Appropriate tools are called based on user intent
6. **Response Generation**: AI generates contextual responses and sends via WhatsApp

## 🔧 Configuration

### MongoDB Vector Search

The notes system uses MongoDB Atlas Vector Search for semantic note retrieval. Ensure your MongoDB cluster has:

- Vector search index configured on the `notes` collection
- Index name: `notes_vector_index`
- Vector field: `embedding`

### WhatsApp Webhook

Configure your WhatsApp webhook to point to:

```
https://your-domain.com/auth/callback
```

## 🧪 Testing

Run the test suite:

```bash
python -m pytest test/
```

## 📱 Usage Examples

### Calendar Management

```
User: "Schedule lunch with Sarah tomorrow at 1pm"
Assistant: Creates Google Calendar event and confirms details

User: "What's on my calendar today?"
Assistant: Shows all events for today with times
```

### Task Management

```
User: "Add task: Submit project report by Friday"
Assistant: Creates high-priority task and confirms

User: "Show my tasks"
Assistant: Displays tasks organized by status and priority
```

### Reminders

```
User: "Remind me 30 minutes before my meeting"
Assistant: Sets up event reminder

User: "Remind me in 2 hours to call mom"
Assistant: Creates custom reminder
```

### Notes

Small test

```
User: "Save this: Meeting notes from today's standup..."
Assistant: "Would you like me to save this as a note?"
User: "Yes"
Assistant: Creates note with AI-generated title and semantic search capability
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- OpenAI for GPT-4 and embeddings
- Google for Calendar API
- MongoDB for database and vector search
- FastAPI for the excellent web framework

## 📞 Support

For support, email support@lofy-assistant.com or create an issue in this repository.

---

**Built with ❤️ for productivity and organization**
