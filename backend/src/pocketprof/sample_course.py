"""Offline sample course: CS-2001 Data Structures, weeks 3 to 6.

The material is generated as real PPTX, PDF, DOCX and text files and enters through the normal upload
path, so the sample exercises the same parsers, chunker, ledger and index as a Classroom course. It is
also the corpus behind the `ds-v1` evaluation query set (eval/querysets/ds-v1.yaml).
"""
from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta, timezone

SLIDES_W3 = [
    ("Stacks and Queues", "CS-2001 Data Structures, Week 3."),
    ("What is a stack?", "A stack is a linear data structure that follows LIFO: Last In, First Out. The last element "
                         "pushed is the first one popped. Real-life example: a pile of plates."),
    ("Stack operations", "push(x) adds x to the top; pop() removes and returns the top; peek() returns the top without "
                         "removing it; isEmpty() checks whether the stack is empty. All four run in O(1) time with "
                         "an array or linked-list implementation."),
    ("Overflow and underflow", "Stack overflow happens when you push onto a full array-based stack. Stack underflow "
                               "happens when you pop from an empty stack. Always check isEmpty() before pop()."),
    ("Applications of stacks", "Undo in editors, the browser back button, the function call stack, checking balanced "
                               "parentheses, converting infix expressions to postfix, and evaluating postfix "
                               "expressions."),
    ("Queues", "A queue follows FIFO: First In, First Out. enqueue(x) adds at the rear, dequeue() removes from the "
               "front. Example: a line at the bank counter."),
    ("Circular queue", "In an array queue, after several dequeues the front cells are wasted. A circular queue wraps "
                       "the rear index around using rear = (rear + 1) % capacity, so freed cells are reused. The "
                       "queue is full when (rear + 1) % capacity == front."),
    ("Stack vs queue", "A stack inserts and removes at the same end (top). A queue inserts at the rear and removes at "
                       "the front. Use a stack for backtracking and nesting; use a queue for scheduling and "
                       "breadth-first search."),
]

PAGES_W4 = [
    "Binary Search Tree (BST)\n\nA BST is a binary tree in which, for every node, all keys in the left subtree are "
    "smaller than the node's key and all keys in the right subtree are larger. Duplicates are not allowed in this "
    "course's definition.",
    "Searching a BST\n\nStart at the root; if the key equals the node, stop; if it is smaller go left, otherwise go "
    "right. Search, insert and delete take O(h) time where h is the height. For a balanced tree h = O(log n); for a "
    "degenerate (skewed) tree, such as one built from sorted input, h = O(n).",
    "Deleting a node\n\nDeleting a node has three cases. Case 1: the node is a leaf, so simply remove it. Case 2: the "
    "node has one child, so replace the node with its child. Case 3: the node has two children, so replace its key "
    "with its in-order successor (the smallest key in the right subtree) and then delete that successor, which has "
    "at most one child.",
    "Traversals\n\nIn-order (left, node, right) visits a BST's keys in sorted ascending order. Pre-order (node, left, "
    "right) is used to copy a tree. Post-order (left, right, node) is used to delete a tree. Level-order traversal "
    "uses a queue.",
]

SLIDES_W5 = [
    ("Linked Lists", "CS-2001 Data Structures, Week 5. Read these notes before Thursday's lab."),
    ("Singly linked list", "A singly linked list is a chain of nodes. Each node stores a value and a pointer called "
                           "next to the following node. The list is reached through a head pointer; the last node's "
                           "next is null."),
    ("Insertion", "Insert at the head in O(1): make the new node point to the old head, then move head to the new "
                  "node. Inserting after a given node is also O(1). Inserting at the tail is O(n) unless a tail "
                  "pointer is kept."),
    ("Deletion", "To delete the head, save a pointer to it, move head to head->next, then free the saved node. To "
                 "delete any other node, walk the list keeping a pointer to the previous node, set previous->next "
                 "to the node's next, and free the node. Always handle the empty list."),
    ("Arrays vs linked lists", "Arrays give O(1) access by index but O(n) insertion in the middle. Linked lists give "
                               "O(1) insertion once you hold the position but O(n) access by index, and use extra "
                               "memory for the pointers."),
]

HASHING_W6 = """# Hash tables

Hash tables store key-value pairs in an array. A hash function h(key) maps each key to an array index, so
lookup, insertion and deletion take O(1) time on average.

# Collisions

A collision happens when two keys hash to the same index. Separate chaining keeps a linked list at each index
and appends colliding keys to it. Open addressing instead probes for the next free slot: linear probing checks
index + 1, index + 2 and so on.

# Load factor

The load factor is the number of keys divided by the table size. When the load factor passes 0.75 the table is
resized to about twice its size and every key is rehashed into the new table.
"""

ASSIGNMENT_2 = [
    ("Task", "Write a C++ program that reads an expression and reports whether its brackets (), {} and [] are "
             "balanced, using your own array-based Stack class. Do not use std::stack."),
    ("Submission", "Submit a single .cpp file and a one-page report on Google Classroom. Deadline: Friday 24 October "
                   "2026, 11:59 pm. Late submissions lose 10% per day, up to 3 days."),
    ("Marking", "Stack class with push, pop, peek and isEmpty: 4 marks. Correct checker logic: 4 marks. Handling of "
                "overflow, underflow and invalid characters: 1 mark. Report and code comments: 1 mark. Total 10 "
                "marks."),
]

ANNOUNCE_QUIZ2 = ("Quiz 2 next Tuesday\n\nQuiz 2 will be held next Tuesday in class. It covers stacks, queues and "
                  "circular queues (Week 3). It is 20 minutes, closed book, and carries 5% of the total grade. BSTs "
                  "are not included.")
ANNOUNCE_QUIZ3 = ("Quiz 3 on Monday 26 October\n\nQuiz 3 will be held on Monday 26 October 2026 in class. It covers "
                  "linked lists (Week 5) and hashing (Week 6). It is 25 minutes, closed book, and carries 5% of the "
                  "total grade. Bring your student ID card.")
ANNOUNCE_MID2 = ("Mid 2 date and syllabus\n\nYour mid 2 will be on 2nd November 2026. Syllabus: week 5 and week 6 "
                 "(linked lists and hashing). Duration 1 hour.")


def make_pptx(slides: list[tuple[str, str]]) -> bytes:
    from pptx import Presentation

    prs = Presentation()
    for title, body in slides:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_pdf(pages: list[str]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(56, 56, 540, 790), text + "\n\nCS-2001 Data Structures", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(sections: list[tuple[str, str]], title: str) -> bytes:
    from docx import Document

    d = Document()
    d.add_heading(title, level=0)
    for head, body in sections:
        d.add_heading(head, level=1)
        d.add_paragraph(body)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def documents() -> list[dict]:
    """(filename, bytes, topic, week, weekday offset within the week)"""
    return [
        dict(name="Lecture3_Stacks_Queues.pptx", data=make_pptx(SLIDES_W3), topic="Stacks and queues", week=3, day=1),
        dict(name="Assignment2_Brief.docx", data=make_docx(ASSIGNMENT_2, "Assignment 2: Balanced Parentheses Checker"),
             topic="Assignment 2", week=3, day=3),
        dict(name="Quiz2_Announcement.txt", data=ANNOUNCE_QUIZ2.encode(), topic="Quizzes", week=3, day=4),
        dict(name="Week4_BST_Notes.pdf", data=make_pdf(PAGES_W4), topic="Binary search trees", week=4, day=1),
        dict(name="Lecture5_Linked_Lists.pptx", data=make_pptx(SLIDES_W5), topic="Linked lists", week=5, day=1),
        dict(name="Week6_Hashing.md", data=HASHING_W6.encode(), topic="Hashing", week=6, day=1),
        dict(name="Quiz3_Announcement.txt", data=ANNOUNCE_QUIZ3.encode(), topic="Quizzes", week=6, day=3),
        dict(name="Mid2_Announcement.txt", data=ANNOUNCE_MID2.encode(), topic="Exams", week=6, day=4),
    ]


def term_start_for(today: date | None = None) -> date:
    """Week 7 is the current week: all sample material (weeks 3 to 6) is in the past, and 'last week'
    resolves to week 6."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(weeks=6)


def published_at(term_start: date, week: int, day: int) -> datetime:
    d = term_start + timedelta(weeks=week - 1, days=day)
    return datetime.combine(d, time(9, 0), tzinfo=timezone.utc)
