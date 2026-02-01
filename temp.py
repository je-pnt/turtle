

class person:
    def __init__(self, name):
        self.name = name

    def greet(self):
        print(f"Hello, my name is {self.name}.")

class employee(person):
    def __init__(self, name, employee_id):
        super().__init__(name)
        self.employee_id = employee_id

    def work(self):
        print(f"{self.name} is working with employee ID: {self.employee_id}.")

if __name__ == "__main__":
    emp = employee("Alice", "E123")
    emp.greet()
    emp.work()

    # john = person("John")
    # print(john.name)
    # john.greet()