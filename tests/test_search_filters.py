"""Scope must constrain each candidate source before its top-k limit."""
import datetime as dt
import json

import numpy as np
import pytest

from atlasbrain import db, embed
from atlasbrain.search import Searcher


def add(con, path, text, *, title='Entry', kind='nota', fm=None, tag=None,
        vector=None, symbol=None, heading='', ord=0, note_id=None):
    if note_id is None:
        note_id = con.execute(
            'INSERT INTO notes(path,title,kind,frontmatter,mtime,size) VALUES(?,?,?,?,?,?)',
            (path, title, kind, json.dumps(fm or {}), dt.datetime(2026, 9, 30).timestamp(), len(text)),
        ).lastrowid
    cid = con.execute('INSERT INTO chunks(note_id,ord,heading,text,title,embedding) VALUES(?,?,?,?,?,?)',
                      (note_id, ord, heading, text, title, db.to_blob(vector) if vector is not None else None)).lastrowid
    con.execute('INSERT INTO chunks_fts(rowid,title,heading,text) VALUES(?,?,?,?)', (cid,title,heading,text))
    if tag:
        con.execute('INSERT INTO tags(note_id,tag) VALUES(?,?)', (note_id,tag))
    if symbol:
        con.execute('INSERT INTO simbolos(note_id,nome,tipo,linha) VALUES(?,?,?,?)', (note_id,symbol,'function',1))
    return note_id


SCOPES = [
    ('pasta:scope', {}, {}),
    ('tag:chosen', {}, {'tag':'chosen'}),
    ('tipo:decisao', {}, {'fm':{'tipo':'decisao'}}),
    ('status:revogada', {}, {'fm':{'status':'revogada'}}),
    ('desde:2026-09', {'fm':{'data':'2025-01-01'}}, {}),
    ('-legacy', {'text':'needle legacy'}, {'text':'needle answer'}),
    ('"approved answer"', {'text':'needle approved mention'}, {'text':'needle approved answer'}),
    ('pasta:scope tag:chosen tipo:decisao status:ativa desde:2026-09 -legacy "approved answer"',
     {'text':'needle legacy approved mention'},
     {'text':'needle approved answer','tag':'chosen','fm':{'tipo':'decisao','status':'ativa','data':'2026-09-30'}}),
]


@pytest.mark.parametrize('source', ['fts','vec','struct'])
@pytest.mark.parametrize('scope,noise_options,answer_options', SCOPES)
def test_candidate_limits_apply_inside_scope(tmp_path, monkeypatch, source, scope, noise_options, answer_options):
    con = db.connect(tmp_path)
    try:
        for i in range(205):
            options = dict(noise_options)
            text = options.pop('text', 'needle answer')
            add(con, f'outside/{i:03}.md', text, vector=[1.,0.],symbol='needle',heading='needle',**options)
        options = dict(answer_options)
        text = options.pop('text', 'needle answer')
        add(con, 'scope/answer.md', text, vector=[.6,.8],symbol='needle',heading='needle',**options)
        con.commit()
        monkeypatch.setattr(embed, 'enabled', lambda: source=='vec')
        monkeypatch.setattr(embed, 'embed', lambda texts: np.array([[1.,0.]],dtype=np.float32))
        hits = Searcher(con).search('needle '+scope, sinais=(source,))
        assert [h['path'] for h in hits] == ['scope/answer.md']
    finally:
        con.close()


def test_exact_match_selects_an_eligible_passage(tmp_path):
    con=db.connect(tmp_path)
    try:
        nid=add(con,'scope/answer.md','legacy needle',ord=-1)
        add(con,'scope/answer.md','approved answer needle',ord=0,note_id=nid)
        add(con,'outside/answer.md','approved answer needle')
        con.commit()
        hits=Searcher(con).search('answer.md pasta:scope -legacy', sinais=('exact',))
        assert [h['path'] for h in hits]==['scope/answer.md']
        assert hits[0]['exato']
        assert 'approved answer' in hits[0]['snippet']
    finally:
        con.close()


def test_folder_boundaries_unicode_and_literal_wildcards(tmp_path):
    con=db.connect(tmp_path)
    try:
        for path in ['ÁREA/a.md','ÁREA-extra/a.md','a_b/a.md','axb/a.md','100%/a.md','100x/a.md']:
            add(con,path,'needle')
        con.commit(); s=Searcher(con)
        assert [h['path'] for h in s.search('needle pasta:"área/"')]==['ÁREA/a.md']
        assert [h['path'] for h in s.search('needle path:a_b')]==['a_b/a.md']
        assert [h['path'] for h in s.search('needle pasta:100%')]==['100%/a.md']
        assert len(s.search('needle pasta:/'))==6
        assert len(s.search('pasta:/'))==6
        assert [h['path'] for h in s.search('needle pasta:axb',folder='a_b')]==['a_b/a.md']
    finally:
        con.close()


def test_filters_only_are_bounded_without_empty_query_embeddings(tmp_path, monkeypatch):
    con=db.connect(tmp_path)
    try:
        nid=add(con,'scope/old.md','one',fm={'tipo':'decisao'},tag='chosen')
        for i in range(220):
            add(con,'scope/old.md','more',ord=i+1,note_id=nid)
        add(con,'scope/new.md','two',fm={'tipo':'decisao'},tag='chosen')
        add(con,'outside/no.md','three')
        con.execute("UPDATE notes SET mtime=0 WHERE path='scope/old.md'")
        con.commit()
        monkeypatch.setattr(embed,'embed',lambda texts: pytest.fail('An empty query must not be embedded'))
        s=Searcher(con)
        hits=s.search('pasta:scope tag:#CHOSEN tipo:decisões',limit=1)
        assert [h['path'] for h in hits]==['scope/new.md']
        assert hits[0]['motivos']==['filtros da consulta']
        assert len(s.search('pasta:scope tipo:decisoes'))==2
        assert not s.search('')
        assert not s.search('pasta:missing')
    finally:
        con.close()


def test_phrase_exclusion_and_dates_remain_chunk_level(tmp_path):
    con=db.connect(tmp_path)
    try:
        nid=add(con,'a.md','needle legacy',fm={'data':'2025-01-01'})
        add(con,'a.md','needle ÁRVORE approved answer',ord=1,note_id=nid)
        add(con,'b.md','needle ÁRVORE approved answer')
        con.commit(); s=Searcher(con)
        assert [h['path'] for h in s.search('needle -legacy "árvore approved" desde:2026-09')]==['b.md']
        assert len(s.search('needle -legacy "árvore approved"'))==2
        assert not s.search('needle "missing phrase"')
    finally:
        con.close()
