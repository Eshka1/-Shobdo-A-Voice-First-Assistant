from app.extensions import mongo
from bson import ObjectId
from datetime import datetime

class Reminder:
    collection = 'reminders'
    
    @staticmethod
    def create(user_id, title, remind_at, description=''):
        """Create a new reminder for a specific user"""
        reminder_data = {
            'user_id': ObjectId(user_id),  # Ownership link
            'title': title,
            'description': description,
            'remind_at': remind_at,  # datetime object
            'status': 'pending',  # pending, completed, cancelled
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }
        
        result = mongo.db[Reminder.collection].insert_one(reminder_data)
        return str(result.inserted_id)
    
    @staticmethod
    def find_by_id(reminder_id, user_id):
        """Find reminder by ID, but ONLY if it belongs to this user (prevents IDOR)"""
        try:
            return mongo.db[Reminder.collection].find_one({
                '_id': ObjectId(reminder_id),
                'user_id': ObjectId(user_id)  # Security: scope to owner
            })
        except:
            return None
    
    @staticmethod
    def find_by_user(user_id, status=None):
        """Get all reminders for a user, optionally filtered by status"""
        query = {'user_id': ObjectId(user_id)}
        if status:
            query['status'] = status
        
        return list(mongo.db[Reminder.collection].find(query).sort('remind_at', 1))
    
    @staticmethod
    def update_status(reminder_id, user_id, new_status):
        """Update reminder status (complete/cancel), only if owner"""
        result = mongo.db[Reminder.collection].update_one(
            {
                '_id': ObjectId(reminder_id),
                'user_id': ObjectId(user_id)  # Security check
            },
            {
                '$set': {
                    'status': new_status,
                    'updated_at': datetime.utcnow()
                }
            }
        )
        return result.modified_count > 0
    
    @staticmethod
    def delete(reminder_id, user_id):
        """Delete reminder, only if owner"""
        result = mongo.db[Reminder.collection].delete_one({
            '_id': ObjectId(reminder_id),
            'user_id': ObjectId(user_id)
        })
        return result.deleted_count > 0
    
    @staticmethod
    def to_json(reminder):
        """Convert reminder document to JSON"""
        return {
            'id': str(reminder['_id']),
            'title': reminder['title'],
            'description': reminder.get('description', ''),
            'remind_at': reminder['remind_at'].isoformat() if isinstance(reminder['remind_at'], datetime) else reminder['remind_at'],
            'status': reminder['status'],
            'created_at': reminder['created_at'].isoformat() if isinstance(reminder['created_at'], datetime) else reminder['created_at']
        }
