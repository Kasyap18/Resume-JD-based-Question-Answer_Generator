from flask import Flask, render_template, request, jsonify
import os
from PyPDF2 import PdfReader
import traceback
import re
import random
from dotenv import load_dotenv
import google.generativeai as genai

app = Flask(__name__)
UPLOAD_FOLDER = 'resumes'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load environment variables
load_dotenv()

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY", "AIzaSyAZ7L57Pjv-TPIb4yd0GPcaElaBRASwjNo"))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    try:
        # Determine the mode (resume-based or JD-based)
        mode = request.form.get('mode', 'resume')  # 'resume' or 'jd'
        
        # Get the number of questions from sliders
        tech_count = int(request.form.get('tech_count', 5))
        nontech_count = int(request.form.get('nontech_count', 5))
        
        print(f"Mode: {mode}")
        print(f"Requested technical questions: {tech_count}")
        print(f"Requested non-technical questions: {nontech_count}")
        
        if mode == 'resume':
            # Resume-based mode
            file = request.files.get('resume')
            if not file or not file.filename.endswith('.pdf'):
                return jsonify({'error': 'Please upload a valid PDF resume.'})
            
            filepath = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(filepath)
            resume_text = extract_text(filepath)
            
            # Extract skills from resume
            skills = extract_keywords(resume_text)
            print(f"Extracted resume skills: {skills}")
            
            # Generate questions based on resume
            questions = generate_resume_based_questions(resume_text, skills, tech_count, nontech_count)
            
        else:
            # JD-based mode
            job_description = request.form.get('job_description', '').strip()
            if not job_description:
                return jsonify({'error': 'Please provide a job description.'})
            
            # Extract skills from job description
            skills = extract_keywords(job_description)
            print(f"Extracted JD skills: {skills}")
            
            # Generate questions based on job description
            questions = generate_jd_based_questions(job_description, skills, tech_count, nontech_count)
        
        technical_questions, nontechnical_questions = separate_questions(questions)
        
        # Ensure we have the requested number of questions
        if len(technical_questions) < tech_count:
            default_tech = generate_default_technical_questions(
                skills, 
                len(technical_questions), 
                tech_count,
                mode
            )
            technical_questions.extend(default_tech)
        
        if len(nontechnical_questions) < nontech_count:
            default_nontech = generate_default_nontechnical_questions(
                len(nontechnical_questions), 
                nontech_count,
                mode
            )
            nontechnical_questions.extend(default_nontech)
        
        # Limit to requested number if we have too many
        technical_questions = technical_questions[:tech_count]
        nontechnical_questions = nontechnical_questions[:nontech_count]
        
        print(f"Final technical questions: {len(technical_questions)}")
        print(f"Final non-technical questions: {len(nontechnical_questions)}")
        
        # Return JSON response
        return jsonify({
            'technical_questions': technical_questions,
            'nontechnical_questions': nontechnical_questions,
            'skills_found': skills,
            'mode': mode
        })
            
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"Error in upload: {error_details}")
        return jsonify({'error': f"An error occurred: {str(e)}"})

@app.route('/generate_answer', methods=['POST'])
def generate_answer():
    try:
        data = request.get_json()
        question = data.get('question')
        mode = data.get('mode', 'resume')
        context = data.get('context', '')  # Resume text or JD text
        
        if not question:
            return jsonify({'error': 'No question provided'}), 400

        # Generate answer using Gemini with context
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            # Extract the question text (remove the numbering)
            question_text = re.sub(r'^\d+\.\s*', '', question)
            
            # Create context-aware prompt
            context_prompt = ""
            if context:
                if mode == 'resume':
                    context_prompt = f"\nCandidate's Background (from resume): {context[:800]}\n"
                else:
                    context_prompt = f"\nJob Requirements: {context[:800]}\n"
            
            prompt = f"""You are an expert interviewer and career coach. 
            Provide a concise, professional answer to this interview question: "{question_text}"
            {context_prompt}
            Your answer must:
            1. Be direct and to the point
            2. Not exceed 100 words
            3. Include key points only
            4. Be in a professional tone
            5. Avoid unnecessary introductions or conclusions
            6. If context is provided, tailor the answer to be relevant to that background/role
            """
            
            response = model.generate_content(prompt)
            
            if response and hasattr(response, 'text'):
                answer = response.text.strip()
                if answer:
                    return jsonify({'answer': answer})
            
            return jsonify({'error': 'Could not generate a valid answer'}), 500
                
        except Exception as api_error:
            print(f"Gemini API Error: {str(api_error)}")
            return jsonify({
                'error': 'Failed to generate answer. Please try again.',
                'details': str(api_error)
            }), 500
            
    except Exception as e:
        print(f"General Error in generate_answer: {str(e)}")
        return jsonify({
            'error': 'An unexpected error occurred',
            'details': str(e)
        }), 500

def separate_questions(questions_text):
    lines = questions_text.split('\n')
    
    technical_questions = []
    nontechnical_questions = []
    current_section = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if 'technical questions' in line.lower() or 'technical:' in line.lower():
            current_section = 'technical'
            continue
        elif 'non-technical' in line.lower() or 'hr questions' in line.lower() or 'hr:' in line.lower():
            current_section = 'nontechnical'
            continue
            
        if current_section == 'technical' and (line[0].isdigit() or re.match(r'^\d+\.', line)):
            technical_questions.append(line)
        elif current_section == 'nontechnical' and (line[0].isdigit() or re.match(r'^\d+\.', line)):
            nontechnical_questions.append(line)
    
    return technical_questions, nontechnical_questions

def extract_text(pdf_path):
    reader = PdfReader(pdf_path)
    text = ''
    for page in reader.pages:
        text += page.extract_text() or ''
    return text

def generate_resume_based_questions(resume_text, skills, tech_count=5, nontech_count=5):
    """Generate questions based on resume content and skills"""
    # Try AI generation first
    api_questions = generate_questions_with_ai(resume_text, skills, tech_count, nontech_count, mode='resume')
    
    if api_questions:
        return api_questions
    
    # Fallback to template-based generation
    return generate_template_questions(skills, tech_count, nontech_count, mode='resume')

def generate_jd_based_questions(job_description, skills, tech_count=5, nontech_count=5):
    """Generate questions based on job description and required skills"""
    # Try AI generation first
    api_questions = generate_questions_with_ai(job_description, skills, tech_count, nontech_count, mode='jd')
    
    if api_questions:
        return api_questions
    
    # Fallback to template-based generation
    return generate_template_questions(skills, tech_count, nontech_count, mode='jd')

def generate_questions_with_ai(content, skills, tech_count=5, nontech_count=5, mode='resume'):
    """Generate questions using Gemini API"""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        skills_text = ", ".join(skills[:15]) if skills else "general skills"
        
        if mode == 'resume':
            prompt = f"""You are an expert technical interviewer. Based on this candidate's resume, generate interview questions that assess their experience and skills.

            Key skills identified: {skills_text}
            
            Generate:
            - Exactly {tech_count} technical questions that test the candidate's expertise in their mentioned skills
            - Exactly {nontech_count} behavioral/HR questions that explore their experience and fit
            
            Make questions specific to their background and challenging but fair.
            
            Format your response exactly like this:
            Technical Questions:
            1. [Technical question 1]
            2. [Technical question 2]
            ...
            
            Non-Technical/HR Questions:
            1. [Non-technical question 1]
            2. [Non-technical question 2]
            ...
            
            Resume Content:
            {content[:2500]}
            """
        else:  # JD mode
            prompt = f"""You are an expert technical interviewer. Based on this job description, generate interview questions that assess if candidates meet the role requirements.

            Key skills required: {skills_text}
            
            Generate:
            - Exactly {tech_count} technical questions that test the required technical skills for this role
            - Exactly {nontech_count} behavioral/cultural fit questions relevant to this position
            
            Make questions specific to the job requirements and assess both technical competency and role fit.
            
            Format your response exactly like this:
            Technical Questions:
            1. [Technical question 1]
            2. [Technical question 2]
            ...
            
            Non-Technical/HR Questions:
            1. [Non-technical question 1]
            2. [Non-technical question 2]
            ...
            
            Job Description:
            {content[:2500]}
            """
        
        response = model.generate_content(prompt)
        
        if response and hasattr(response, 'text'):
            return response.text
        return None
    except Exception as e:
        print(f"Gemini API Error: {str(e)}")
        return None

def generate_template_questions(skills, tech_count, nontech_count, mode='resume'):
    """Generate questions using templates when AI fails"""
    questions = []
    
    # Technical question templates
    if mode == 'resume':
        tech_templates = [
            "Can you describe your experience with {skill} and provide specific examples?",
            "What challenges did you face while working with {skill} in your previous roles?",
            "How have you applied {skill} to solve complex problems?",
            "Can you walk me through a project where you used {skill} extensively?",
            "What best practices do you follow when working with {skill}?",
        ]
    else:  # JD mode
        tech_templates = [
            "How would you use {skill} to meet the requirements of this role?",
            "What's your experience level with {skill} and how does it apply here?",
            "Can you solve a problem using {skill} that's relevant to this position?",
            "How do you stay current with {skill} developments in the industry?",
            "What would be your approach to implementing {skill} in our environment?",
        ]
    
    # Generate technical questions
    tech_questions = []
    all_skills = skills.copy() if skills else ["Programming", "Problem Solving", "Technical Skills"]
    
    for i in range(tech_count):
        skill = all_skills[i % len(all_skills)]
        template = tech_templates[i % len(tech_templates)]
        question = f"{i+1}. {template.format(skill=skill)}"
        tech_questions.append(question)
    
    # Non-technical questions
    if mode == 'resume':
        nontech_questions = [
            "1. Tell me about yourself and your career journey.",
            "2. What motivated you to pursue your current career path?",
            "3. Describe a challenging project from your experience.",
            "4. How do you handle working under pressure?",
            "5. What are your career goals for the next 5 years?",
            "6. Tell me about a time you had to learn something new quickly.",
            "7. How do you approach problem-solving in your work?",
            "8. Describe a situation where you had to work in a team.",
            "9. What's your greatest professional achievement?",
            "10. How do you stay updated with industry trends?"
        ]
    else:  # JD mode
        nontech_questions = [
            "1. Why are you interested in this specific role?",
            "2. How do your skills align with our job requirements?",
            "3. What attracts you to our company and industry?",
            "4. How would you approach the challenges mentioned in this job description?",
            "5. What questions do you have about the role and responsibilities?",
            "6. How do you see yourself contributing to our team?",
            "7. What's your experience with the type of work environment we offer?",
            "8. How do you prioritize tasks when facing multiple deadlines?",
            "9. What would success look like for you in this position?",
            "10. How do you handle feedback and continuous learning?"
        ]
    
    # Combine questions
    questions.append("Technical Questions:")
    questions.extend(tech_questions)
    questions.append("")
    questions.append("Non-Technical/HR Questions:")
    questions.extend(nontech_questions[:nontech_count])
    
    return "\n".join(questions)

def generate_default_technical_questions(skills, current_count, target_count, mode):
    """Generate default technical questions when needed"""
    remaining_needed = target_count - current_count
    if remaining_needed <= 0:
        return []
    
    if mode == 'resume':
        default_tech = [
            f"{current_count+1}. What programming languages are you most proficient in?",
            f"{current_count+2}. Describe a challenging technical problem you solved recently.",
            f"{current_count+3}. How do you approach debugging complex issues?",
            f"{current_count+4}. What development methodologies have you worked with?",
            f"{current_count+5}. How do you ensure code quality in your projects?",
        ]
    else:  # JD mode
        default_tech = [
            f"{current_count+1}. How would you approach the technical challenges in this role?",
            f"{current_count+2}. What technical skills make you suitable for this position?",
            f"{current_count+3}. How do you stay updated with technologies relevant to this job?",
            f"{current_count+4}. What would be your learning plan for this role's requirements?",
            f"{current_count+5}. How do you evaluate and choose technical solutions?",
        ]
    
    return default_tech[:remaining_needed]

def generate_default_nontechnical_questions(current_count, target_count, mode):
    """Generate default non-technical questions when needed"""
    remaining_needed = target_count - current_count
    if remaining_needed <= 0:
        return []
    
    if mode == 'resume':
        default_nontech = [
            f"{current_count+1}. Tell me about yourself.",
            f"{current_count+2}. What are your career goals?",
            f"{current_count+3}. Describe your work style and preferences.",
            f"{current_count+4}. How do you handle challenges at work?",
            f"{current_count+5}. What motivates you professionally?",
        ]
    else:  # JD mode
        default_nontech = [
            f"{current_count+1}. Why are you interested in this position?",
            f"{current_count+2}. How do you fit our company culture?",
            f"{current_count+3}. What attracts you to this industry?",
            f"{current_count+4}. How would you contribute to our team?",
            f"{current_count+5}. What questions do you have about the role?",
        ]
    
    return default_nontech[:remaining_needed]

def extract_keywords(text):
    """Extract skills from text (resume or job description)"""
    # Comprehensive list of skills across various domains
    common_skills = [
        # Programming Languages
        "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "C", "Go", "Rust", "Swift",
        "Kotlin", "PHP", "Ruby", "Scala", "R", "MATLAB", "Perl", "Shell Scripting", "PowerShell",
        
        # Web Technologies
        "HTML", "CSS", "React", "Angular", "Vue.js", "Node.js", "Express.js", "Django", "Flask",
        "Spring Boot", "ASP.NET", "Laravel", "Ruby on Rails", "jQuery", "Bootstrap", "Sass", "Less",
        
        # Databases
        "SQL", "MySQL", "PostgreSQL", "MongoDB", "Redis", "Cassandra", "Oracle", "SQL Server",
        "SQLite", "DynamoDB", "Neo4j", "Elasticsearch", "Database Design", "Database Administration",
        
        # Cloud & DevOps
        "AWS", "Azure", "Google Cloud", "GCP", "Docker", "Kubernetes", "Jenkins", "GitLab CI",
        "GitHub Actions", "Terraform", "Ansible", "Chef", "Puppet", "CI/CD", "DevOps",
        "Microservices", "Serverless", "Lambda", "Cloud Architecture",
        
        # Data Science & AI
        "Machine Learning", "Deep Learning", "Data Science", "Artificial Intelligence", "AI",
        "Natural Language Processing", "NLP", "Computer Vision", "TensorFlow", "PyTorch",
        "Scikit-learn", "Pandas", "NumPy", "Matplotlib", "Seaborn", "Jupyter", "Data Analysis",
        "Statistical Analysis", "Big Data", "Hadoop", "Spark", "Kafka", "Data Mining",
        
        # Mobile Development
        "iOS Development", "Android Development", "React Native", "Flutter", "Xamarin",
        "Mobile App Development", "Swift", "Objective-C", "Kotlin", "Java",
        
        # Tools & Frameworks
        "Git", "GitHub", "GitLab", "Bitbucket", "JIRA", "Confluence", "Slack", "Trello",
        "Visual Studio", "IntelliJ", "Eclipse", "VS Code", "Postman", "Swagger",
        
        # Methodologies
        "Agile", "Scrum", "Kanban", "Waterfall", "Test Driven Development", "TDD",
        "Behavior Driven Development", "BDD", "Continuous Integration", "Continuous Deployment",
        
        # Testing
        "Unit Testing", "Integration Testing", "Automated Testing", "Manual Testing",
        "Selenium", "Jest", "JUnit", "PyTest", "Quality Assurance", "QA",
        
        # Security
        "Cybersecurity", "Information Security", "Network Security", "Penetration Testing",
        "Vulnerability Assessment", "OWASP", "SSL/TLS", "Authentication", "Authorization",
        
        # Business & Soft Skills
        "Project Management", "Team Leadership", "Communication", "Problem Solving",
        "Critical Thinking", "Analytical Skills", "Time Management", "Adaptability",
        "Creativity", "Innovation", "Customer Service", "Sales", "Marketing",
        "Business Analysis", "Requirements Gathering", "Stakeholder Management",
    ]
    
    # Find skills mentioned in the text
    found_skills = []
    text_lower = text.lower()
    
    for skill in common_skills:
        pattern = r'\b' + re.escape(skill.lower()) + r'\b'
        if re.search(pattern, text_lower):
            found_skills.append(skill)
    
    # Look for additional technical terms and acronyms
    acronyms = re.findall(r'\b[A-Z]{2,6}\b', text)
    for acronym in acronyms:
        if acronym not in found_skills and len(acronym) <= 6:
            found_skills.append(acronym)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_skills = []
    for skill in found_skills:
        skill_clean = skill.strip()
        if skill_clean.lower() not in seen and len(skill_clean) > 1:
            seen.add(skill_clean.lower())
            unique_skills.append(skill_clean)
    
    # If no skills found, provide default ones
    if not unique_skills:
        default_skills = ["Communication", "Problem Solving", "Teamwork", 
                         "Project Management", "Leadership", "Critical Thinking"]
        unique_skills = default_skills
    
    # Limit and randomize
    unique_skills = unique_skills[:20]
    random.shuffle(unique_skills)
    
    return unique_skills

if __name__ == '__main__':
    app.run(debug=True)