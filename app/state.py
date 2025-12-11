_state={
    'projector':{'power':'STANDBY','input':'HDMI1'},
    'dsp':{'state':'OK'},
    'shelly':{'main':'OK', 'telo':'OK'},
    'text':'Sistema pronto',
    'current_lesson':''
}

def set_public_state(d): _state.update(d)

def get_public_state(): return _state.copy()
