
---

## Considition 2025 Hackaton, Intelligent EV Routing Planner

This repository contains my solution for the Considition 2025 challenge.
The goal of the competition is to design an algorithm that makes smart charging and routing decisions for a fleet of electric vehicles, taking into account multiple factors such as energy consumption, charger availability, waiting time, sustainability and customer satisfaction.
The challenge includes five customer personas that influence expected behaviour, CostSensitive, DislikesDriving, EcoConscious, Stressed and Neutral.

My objective was to explore how an intelligent and behaviour aware routing system would operate when applied to a synthetic environment. The result is a hybrid solution that blends cooperative pathfinding, personalised decision making and machine learning driven charging policies.

---

## Core Ideas

My approach is inspired by real world mobility optimisation. Instead of relying only on single metric maximisation, the planner attempts to reason about travel flow, waiting times, sustainability preferences and persona specific behaviour. The system is composed of several interacting components working together each tick.

### 1. Cooperative A star and MAPF style corridor planning

The planner computes per customer travel paths using a time expanded A* variant combined with soft and hard reservations. This prevents multiple vehicles from overcrowding the same edge or station area, creates flow aware movement patterns and reduces unnecessary conflicts. When needed the planner falls back to classic A star for speed or for stressed personas.

### 2. Behaviour aware charging logic

Customers receive dynamic charging recommendations based on:

* Distance to goal
* Consumption rate
* Station characteristics
* Queue pressure
* Predicted waiting time
* Persona behaviour patterns

Micro charging is used for stressed customers or customers passing favourable stations. EcoConscious customers prefer greener stations when possible. DislikesDriving and Stressed customers are more sensitive to waiting time. CostSensitive customers use energy more conservatively in the policy training phase.

### 3. XGBoost policy layer

A lightweight XGBoost model provides a data driven signal on whether a customer should charge or skip when passing a candidate station. The model is trained from historical events with persona aware reward shaping. The policy does not replace the planner, it adjusts the charging decision within the corridor planning framework.

The policy features include:

* is_green
* station_speed_kw
* queue_len
* soc
* dist_to_goal_km
* action_is_charge
* persona one hot indicators

Persona based reward shaping influences the learning. CostSensitive persona reduces weight on kWh gains, EcoConscious persona increases reward at green stations, Stressed and DislikesDriving personas increase waiting penalties.

### 4. Integrated flow control

All charging reservations, edge reservations and customer actions update a shared reservation ledger. This creates a consistent emergent behaviour where the planner minimises collisions, long queues and unnecessary detours.

---

## Implementation Structure

```
app.py                       Entry point, tick loop, API client integration
ml_planner/planner_xgb.py    FlowAwarePlanner subclass with XGBoost policy integration
planner/planner.py           Core flow aware planner, CA star, reservations
policy_trainer.py            Automatic training of the XGB policy
data/charging_events.csv     Optional training data source
model_artifacts/             Stored model and feature set
```

The system can run purely on heuristics if the model is missing. When a trained policy exists it influences only the decision to charge or skip while the routing logic remains deterministic and flow aware.

---

## Result and Reflections

My final score placed me at rank 42 in the competition.
Although not a leaderboard winning score, the solution provided insights into the behaviour of intelligent routing when operating on synthetic environments with uniform structure.
The objective of this project was to design a solution that behaves logically, consistently and contextually rather than purely maximising short term metrics.
From that perspective the implementation succeeded in exploring how behavioural logic, cooperative pathfinding and machine learning can interact inside a multi agent mobility simulation.

---

## Running the Planner

```
python app.py
```

Environment variables in `.env` control behaviour, including policy training, API keys and simulation mode.

---

## License

This project is open for educational and experimental use.

---


