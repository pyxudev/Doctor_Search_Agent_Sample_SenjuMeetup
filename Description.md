# Health Care call center AI agent

## Why building this?

Currently our call center is peforming low efficent on dealing with phone calls and text contact from patients
They were supposed to provide best fit doctor according to the patient description
This project is to reduce human process on the workflow and reduce the time from call-in to suggestion provided

## Business Workflow

1. The patient will contact us with SMS text message or phone call
2. The operator will check the rquest and understand situation of the patient
3. Ask for questions if more information required
4. Final confirmation with the patient
5. Search in the database
6. Find most matched doctor and provide reservation

## Goal

- Reduce the time between call-in starts and suggestion provided
- Higher CSAT
- Higher accuracy%

## Inpurt

- SMS text message or Voice call
- Keywords: Gender, Illness, Region, Personality, Available datetime

## Output

- Provied correct doctor information including: Name, Expertise, Region, Gender, Hoispital, Personality 

## Keywords

- Doctor name
- Expertise
- Gender
- Region
- Available date and time slots
- Personality(kind, humor, professional...)

## System Workflow
    
    ```
    SMS text message / Voice Call-in, start the process
        ↓
    Twilio 
        ↓
    Speech to Text
        ↓                                            If no match/hard to understand
    Input Validation   -----------------|-----|-----|-----> Escalation
    ・Confidence Score                  |     |     |
    ・Medical term                      |     |     |
    ・Fuzzy Matching                  loop   loop   |
    ・Confirmation flow                 |     |     |
        ↓                               |     |     |
    Ask questions and confirmation  ----|     |     |
        ↓                                     |     |
    Final Confirmation -----------------------|     |
        ↓                                           |
    Intent / Planner                                |
        ↓                                         loop
    Doctor Search Tool                              |
            ├─ Structured Search                    |
            └─ Semantic Search                      |
        ↓                                           |
    LLM Re-ranking and Response                     |
        ↓                                           |
    Text to Speech / SMS Response                   |
        ↓                                   Retry   | If still not satisfied
    Confirm with patient to reserve or not  --------|---->  Escalation
        ↓ If yes            ↓ Finish the call or message session timeout
    Reserve Doctor          ↓
            ↓               ↓
             End the process
    ```

## Technologies

### Requirements

- Docker
- Python
- MongoDB/PostgreSQL
- Twilio API
- LLM API

### Components

1. Call

    - Options: Twilio/Amazon Connect/Azure Communication Services/Others based on existing system
    - For PoC, we can use Twilio, good supported APIs and functions
    - For prod, depend on client existing environment

2. Text/Speech to text

    - LLM
    - quality:
        -  `Confidence` > 0.95 or confirm with the patient
        - Levenshtein Distance → Alias value    
            ```
            ENT
            ↓
            Otolaryngology
            
            Pedia
            ↓
            Pediatrics
            ```
    
3. Data Ingestion
    
    ```
    JSON
    ↓
    Validation
    ↓
    Normalizer
    ↓
    DB
    ↓
    Set Index
    ```

4. DB
    - SQLite - POC
    - PostgreSQL - Production
        - Full Text Search
        - JSONB
        - pgvector

    #### Tables

    - Audit log table
        - Id, Audit Datetime, Process name, Table name, Value before, Value after, operator name

    - Doctor information table
        - Id, Name, Gender, Age, Expertise, Hospital, Region, Language, Internal number, Rating, Score, Personality, Register date, Leave date

    - Hospital information table
        - Id, Name, Region, Rating, Address, Internal number

    - Keywords and Alias list table
        - Keywords, Alias

    - Operator information table
        - Name, Gender, Role, Employee Id, Phone Number, Join date, Leave date, role

    - Contact log table
        - Id, Start time, End time, Phone number, Keywords, Doctor name, IsAccurate, CSAT, TokenCost

    #### Roles(Not required in Poc Phase)

    - Admin/Agent
        - Can read, create and update all table
        - Can delete data in Operator information table, Keywords and Alias list table, Hospital table and Doctor information table

    - Manager
        - Can read, create, update and delete data in Operator information table, Keywords and Alias list table, Hospital table and Doctor information table
        - Can read all tables

    - Operator
        - Can read and update Doctor information table and keywords data table
        - Can only access personal Operator information row

    #### CURD
    - Create
        - When a new operator joined, create new row on Operator auth table and Operator information table
        - When a new doctor registered, create new row on Doctor information table
        - When a new keyword includes in Doctor information table after updated or created, create new keyword in Keywords and Alias list table
        - When an process started, create a new row in Audit log table and Contact log table

    - Update
        - When an process finished, update Contact log table with udpating End time
        - When an operator has left, update Leave date in Operator information table
        - When a new Alias been detected in an process, update Keywords and Alias list table to add it to where key = the keyword
        - When the process finsihed, update TokenCost and IsAccurate with the patient's reaction
            - If the reservation has been confirmed, log IsAccurate as `1`
            - If been rejected, log IsAccurate as `0`
        - When a CSAT has been answered, update the score to Contact log table
        - When a Manager or an Admin role wants to modify operator information, update Operator information table
        - When a Manager or an Admin role wants to modify Doctor information, update Doctor information table
        - When a doctor wants to leave the network, update Leave date in Doctor information table

    - Read
        - When a Manager or an Admin role wants to check Audit, read Audit log table
        - When an any-role user wants to check Operator information, read Operator information table 
        - When the Agent is doing Input Validation phase, read Keywords and Alias list table
        - When the Agent is calling Doctor Search Tool, read Doctor information table
        - When The patient wants to reserve a doctor, read the Doctor information table

    - Delete
        - When an Admin role wants to delete an operator, delete Operator information row
        - When an Admin role wants to delete an Doctor, delete Doctor information row
        - When an Admin role wants to delete a Keyword or an Alias, delete Keywords and Alias list row

5. Search and AI Agent

    #### Search

    - Hybrid Search
        - If the order is Structured Query, just do a normal sql search by the keywords
        - If the order is more verbal, the Agent will convert the request into related keywords in keywords table

    #### Agent

    ```
    Voice
    ↓
    STT
    ↓
    Intent Detection
    ↓
    Planner
    ↓
    Tool Calling
    ↓
    FindDoctor() (Region, gender, expertise)
    ↓
    Re-ranking (Region, available time, )
    ↓
    Response
    ```

6. Operator login(Not required in Poc Phase)

    - Auth on Congnito/Azure AD/ Custom user pool auth

7. Security
    
    - Access level control
    - Audit Log
    - DB Encrypt
    - Avoid prompt injection
    - Law limitation
    
8. Network and Infra

    - Since it does not allowed to transfer data to cloud, we will need to use an on-promise server or a private cloud
    - For PoC phase just provide docker images

9. Jobs and Batches

    - New JSON data ingestion Job
    - Auto-updating database
    - Email/Application notify Job
    - App/network health check Job
    - Performance reporting Job
    
10. Variables

    Manage at `.env` file
    - LLM API key:
    - Twilio API key:
    - Response time out: 120s
    - Retry attempt: 1 
    - Max Search time: 60s
    - Log level: info

## Farther optimization in the future
- Agent Skills
- `Agent.md` optimization
- Architecture modification
- Cache: monitor availability and concurrency, design cache storing if required.
